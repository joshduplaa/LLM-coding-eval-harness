"""Unit tests for the Kubernetes pod spec. No cluster needed."""

import json

from evalharness.k8s_sandbox import kubectl_run_args, pod_overrides
from evalharness.sandbox import SandboxLimits

LIMITS = SandboxLimits(timeout_s=5, memory_mb=256, cpus=1.0, tmpfs_mb=32)


def test_pod_spec_has_every_hardening_setting():
    spec = pod_overrides("p", "img:1", LIMITS)["spec"]
    assert spec["restartPolicy"] == "Never"
    assert spec["automountServiceAccountToken"] is False
    assert spec["enableServiceLinks"] is False
    assert spec["dnsPolicy"] == "None" and spec["dnsConfig"]["nameservers"] == ["127.0.0.1"]
    assert spec["activeDeadlineSeconds"] > LIMITS.timeout_s
    psc = spec["securityContext"]
    assert psc["runAsNonRoot"] is True and psc["runAsUser"] == 10001 and psc["runAsGroup"] == 10001
    assert psc["seccompProfile"] == {"type": "RuntimeDefault"}

    (c,) = spec["containers"]
    sc = c["securityContext"]
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["readOnlyRootFilesystem"] is True
    assert sc["privileged"] is False
    assert sc["capabilities"] == {"drop": ["ALL"]}
    assert c["resources"]["limits"] == {"cpu": "1.0", "memory": "256Mi"}
    assert c["resources"]["requests"] == c["resources"]["limits"]
    assert c["imagePullPolicy"] == "Never"
    assert c["stdin"] is True and c["stdinOnce"] is True and c["tty"] is False
    assert "env" not in c and "envFrom" not in c  # nothing from the host gets in

    vols = {v["name"]: v for v in spec["volumes"]}
    assert set(vols) == {"tmp", "out"}  # scratch only: no hostPath, secret, or configMap
    assert vols["tmp"]["emptyDir"] == {"medium": "Memory", "sizeLimit": "32Mi"}
    assert {m["mountPath"] for m in c["volumeMounts"]} == {"/tmp", "/harness-out"}


def test_kubectl_run_passes_no_secrets_on_the_command_line():
    args = kubectl_run_args("p", "img:1", LIMITS, "ns")
    assert args[:3] == ["kubectl", "run", "p"]
    assert "--restart=Never" in args and "--stdin" in args
    assert not any(a.startswith(("--env", "--tty")) or a == "-t" for a in args)
    overrides = json.loads(args[args.index("--overrides") + 1])
    assert overrides == pod_overrides("p", "img:1", LIMITS)


# ---- the in-pod egress gate (sandbox/entrypoint.py) fails closed ----

import importlib.util  # noqa: E402
import socket  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def entrypoint():
    path = Path(__file__).resolve().parents[1] / "sandbox" / "entrypoint.py"
    spec = importlib.util.spec_from_file_location("sandbox_entrypoint", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # trusted harness code, not model output
    return mod


def test_gate_refuses_to_proceed_while_network_is_reachable(entrypoint):
    with socket.create_server(("127.0.0.1", 0)) as srv:
        srv.listen(64)
        port = srv.getsockname()[1]
        problem = entrypoint._wait_for_egress_block(
            {"host": "127.0.0.1", "port": port, "max_wait_s": 1}
        )
    assert problem and "not in force" in problem


def test_gate_does_not_treat_refused_as_blocked(entrypoint):
    with socket.create_server(("127.0.0.1", 0)) as srv:
        port = srv.getsockname()[1]
    # Nothing listens now: connections are refused, which says nothing about a policy.
    gate = {"host": "127.0.0.1", "port": port, "max_wait_s": 1}
    problem = entrypoint._wait_for_egress_block(gate)
    assert problem


def test_pod_deadline_covers_the_gate():
    from evalharness.k8s_sandbox import EGRESS_GATE_MAX_WAIT_S

    spec = pod_overrides("p", "img:1", LIMITS)["spec"]
    assert spec["activeDeadlineSeconds"] > LIMITS.timeout_s + EGRESS_GATE_MAX_WAIT_S


def test_dedicated_sandbox_pool_and_runtime_class_are_opt_in(monkeypatch):
    from evalharness import k8s_sandbox

    plain = pod_overrides("p", "img:1", LIMITS)["spec"]
    assert "runtimeClassName" not in plain and "nodeSelector" not in plain
    assert "tolerations" not in plain

    monkeypatch.setattr(k8s_sandbox, "RUNTIME_CLASS", "kata-vm-isolation")
    monkeypatch.setattr(k8s_sandbox, "NODE_POOL", "sandbox")
    spec = k8s_sandbox.pod_overrides("p", "img:1", LIMITS)["spec"]
    assert spec["runtimeClassName"] == "kata-vm-isolation"
    assert spec["nodeSelector"] == {"evalharness.io/pool": "sandbox"}
    (tol,) = spec["tolerations"]
    # Tolerates exactly the sandbox taint from infra/azure, not every taint.
    assert tol == {
        "key": "evalharness.io/sandbox",
        "operator": "Equal",
        "value": "true",
        "effect": "NoSchedule",
    }
    # The hardening is unchanged.
    assert spec["securityContext"] == plain["securityContext"]
    assert spec["containers"] == plain["containers"]
