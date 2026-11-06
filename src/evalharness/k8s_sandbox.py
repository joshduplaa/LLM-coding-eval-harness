"""Kubernetes sandbox backend: each sample runs in its own throwaway, locked-down pod (D20).

Mapping from the Docker backend (sandbox.docker_run_args) to Kubernetes:

    --network none          NetworkPolicy "deny-all" in the sandbox namespace (deploy/k8s), plus
                            DNS pointed at 127.0.0.1 so name lookups fail instantly
    --read-only             securityContext.readOnlyRootFilesystem
    --tmpfs /tmp, /harness-out   memory-backed emptyDir volumes with sizeLimit
    --user 10001:10001      runAsUser/runAsGroup 10001, runAsNonRoot
    --cap-drop ALL          capabilities.drop [ALL]
    no-new-privileges       allowPrivilegeEscalation: false
    seccomp default         seccompProfile RuntimeDefault
    --memory / --cpus       resources.limits (requests == limits)
    --pids-limit            kubelet podPidsLimit (node-wide; deploy/kind/cluster.yaml)
    no secrets              automountServiceAccountToken: false, enableServiceLinks: false,
                            no env, no volumes besides the two scratch dirs
    time                    wrapper deadline, host deadline (kill + delete), activeDeadlineSeconds

The sandbox namespace also enforces the "restricted" Pod Security Standard, so the API server
itself rejects any pod that drops one of these settings.

A NetworkPolicy only takes effect once the network plugin has seen the new pod; until then its
traffic is allowed. So the payload carries an "egress gate": the trusted wrapper waits until a
normally reachable address is blocked before it runs any untrusted code (sandbox/entrypoint.py).

Known gaps vs Docker: emptyDir can't be mounted noexec, there is no per-pod open-files ulimit,
and a network attempt with no socket timeout shows up as `timeout` (packets are dropped, not
refused). See D20.

Files and the nonce still go in over stdin (`kubectl run -i`), never through the pod spec, so the
untrusted code can't read the nonce from its environment or a mounted file.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
import uuid

from .sandbox import (
    SANDBOX_UID,
    STARTUP_GRACE_S,
    SandboxLimits,
    SandboxOutcome,
    _bounded_communicate,
    parse_envelope,
)

NAMESPACE = os.environ.get("EVALHARNESS_SANDBOX_NAMESPACE", "evalharness-sandbox")
# Images are loaded into the cluster by `make k8s-images`; never pull them from a registry.
IMAGE_PULL_POLICY = os.environ.get("EVALHARNESS_SANDBOX_PULL_POLICY", "Never")
POD_START_TIMEOUT_S = 60
# How long the in-pod wrapper waits for the deny-all NetworkPolicy to take effect (D20).
EGRESS_GATE_MAX_WAIT_S = 20
_canary: tuple[str, int] | None = None


def _kubectl(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, timeout=timeout, env={**os.environ}
    )


def pod_overrides(name: str, image: str, limits: SandboxLimits) -> dict:
    uid, gid = (int(x) for x in SANDBOX_UID.split(":"))
    resources = {"cpu": str(limits.cpus), "memory": f"{limits.memory_mb}Mi"}
    return {
        "apiVersion": "v1",
        "metadata": {"labels": {"evalharness": "sandbox"}},
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,  # no Kubernetes API credential in the pod
            "enableServiceLinks": False,  # no env vars describing cluster services
            "activeDeadlineSeconds": int(
                limits.timeout_s + STARTUP_GRACE_S + EGRESS_GATE_MAX_WAIT_S + 30
            ),
            "terminationGracePeriodSeconds": 0,
            "hostname": "sandbox",
            # Name lookups go to a resolver that doesn't exist, so they fail at once.
            "dnsPolicy": "None",
            "dnsConfig": {"nameservers": ["127.0.0.1"]},
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": uid,
                "runAsGroup": gid,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": name,
                    "image": image,
                    "imagePullPolicy": IMAGE_PULL_POLICY,
                    "stdin": True,
                    "stdinOnce": True,  # stdin closes after our one write: the wrapper sees EOF
                    "tty": False,
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "privileged": False,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "resources": {"limits": resources, "requests": resources},
                    "volumeMounts": [
                        {"name": "tmp", "mountPath": "/tmp"},  # noqa: S108 - in-pod path
                        {"name": "out", "mountPath": "/harness-out"},
                    ],
                }
            ],
            "volumes": [
                {
                    "name": "tmp",
                    "emptyDir": {"medium": "Memory", "sizeLimit": f"{limits.tmpfs_mb}Mi"},
                },
                {"name": "out", "emptyDir": {"medium": "Memory", "sizeLimit": "1Mi"}},
            ],
        },
    }


def kubectl_run_args(name: str, image: str, limits: SandboxLimits, namespace: str) -> list[str]:
    return [
        "kubectl",
        "run",
        name,
        "--namespace",
        namespace,
        "--image",
        image,
        "--restart=Never",
        "--stdin",
        "--quiet",
        f"--pod-running-timeout={POD_START_TIMEOUT_S}s",
        "--overrides",
        json.dumps(pod_overrides(name, image, limits)),
    ]


def egress_canary() -> tuple[str, int]:
    """The `kubernetes` Service address: reachable from any pod unless a policy blocks it."""
    global _canary
    if _canary is None:
        r = _kubectl(
            "get", "service", "kubernetes", "--namespace", "default",
            "-o", "jsonpath={.spec.clusterIP} {.spec.ports[0].port}",
        )  # fmt: skip
        if r.returncode != 0:
            raise RuntimeError(f"cannot find the kubernetes Service: {r.stderr.strip()[:300]}")
        host, port = r.stdout.split()
        _canary = (host, int(port))
    return _canary


def run_in_pod(
    files: dict[str, str],
    limits: SandboxLimits,
    image: str,
    namespace: str = NAMESPACE,
) -> SandboxOutcome:
    out = SandboxOutcome(image=image, backend="kubernetes", runtime=None, limits=limits.to_dict())
    name = f"evalh-{uuid.uuid4().hex[:12]}"
    nonce = secrets.token_hex(16)
    try:
        host, port = egress_canary()
    except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        out.harness_error = f"egress gate unavailable: {e}"
        return out
    gate = {"host": host, "port": port, "max_wait_s": EGRESS_GATE_MAX_WAIT_S}
    payload = json.dumps(
        {"files": files, "timeout_s": limits.timeout_s, "nonce": nonce, "egress_gate": gate}
    ).encode()
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            kubectl_run_args(name, image, limits, namespace),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ},  # kubectl's own config; the pod gets none of it
        )
    except FileNotFoundError:
        out.harness_error = "kubectl not found on PATH"
        return out
    try:
        budget = POD_START_TIMEOUT_S + EGRESS_GATE_MAX_WAIT_S + limits.timeout_s + STARTUP_GRACE_S
        deadline = start + budget
        stdout, stderr, timed_out, exceeded = _bounded_communicate(proc, payload, deadline)
        if timed_out or exceeded:
            proc.kill()
            proc.wait()
            _delete(name, namespace)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        out.duration_s = round(time.monotonic() - start, 3)
        out.docker_returncode = proc.returncode  # here: kubectl's exit code
        out.host_timed_out = timed_out
        out.output_exceeded = exceeded
        out.stderr_tail = stderr.decode("utf-8", "replace")[-4000:]

        status = _pod_status(name, namespace)
        term = _terminated(status)
        if status:
            cs = (status.get("containerStatuses") or [{}])[0]
            out.image_id = cs.get("imageID") or None
            out.runtime_used = "kubernetes"
        if term is not None:
            out.oom_killed = term.get("reason") == "OOMKilled"
        elif not (timed_out or exceeded):
            # The container never ran: image missing, quota, admission rejected, ...
            out.harness_error = f"sandbox pod did not run: {_why_not(status, out.stderr_tail)}"
            return out

        out.envelope, out.envelope_problem = parse_envelope(
            stdout.decode("utf-8", "replace"), nonce
        )
        if out.envelope and out.envelope.get("wrapper_error"):
            out.harness_error = f"sandbox wrapper error: {out.envelope['wrapper_error']}"
        return out
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        _delete(name, namespace)


def _delete(name: str, namespace: str) -> None:
    try:
        _kubectl(
            "delete", "pod", name, "--namespace", namespace,
            "--grace-period=0", "--force", "--ignore-not-found", "--wait=false",
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        pass


def _pod_status(name: str, namespace: str) -> dict | None:
    try:
        r = _kubectl("get", "pod", name, "--namespace", namespace, "-o", "json")
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    return json.loads(r.stdout).get("status") or {}


def _terminated(status: dict | None) -> dict | None:
    if not status:
        return None
    for cs in status.get("containerStatuses") or []:
        state = cs.get("state") or {}
        if "terminated" in state:
            return state["terminated"]
        last = (cs.get("lastState") or {}).get("terminated")
        if last:
            return last
    return None


def _why_not(status: dict | None, stderr: str) -> str:
    if status:
        for cs in status.get("containerStatuses") or []:
            waiting = (cs.get("state") or {}).get("waiting")
            if waiting:
                return f"{waiting.get('reason')}: {waiting.get('message', '')}"[:500]
        if status.get("reason"):
            return f"{status['reason']}: {status.get('message', '')}"[:500]
    return stderr.strip()[-500:] or "unknown (no pod status)"


def cluster_ready(namespace: str = NAMESPACE) -> str | None:
    """None if kubectl can reach the cluster and the sandbox namespace exists, else why not."""
    try:
        r = _kubectl("get", "namespace", namespace, "-o", "name", timeout=15)
    except FileNotFoundError:
        return "kubectl not found on PATH"
    except subprocess.TimeoutExpired:
        return "Kubernetes API did not respond"
    if r.returncode != 0:
        return f"cannot use namespace {namespace!r}: {r.stderr.strip()[:300]}"
    return None
