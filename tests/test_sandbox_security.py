"""Adversarial tests: the sandbox must contain hostile code, or record it cleanly as a failure.

Every "solution" here is hostile test data. It's passed to the sandbox as a string and never
imported or run on the host.
"""

import json
import os
from pathlib import Path

import pytest

from evalharness.sandbox import (
    DEFAULT_BACKEND,
    DEFAULT_RUNTIME,
    RUNSC_MIN_PIDS,
    SANDBOX_UID,
    SandboxLimits,
    docker_run_args,
    run_in_sandbox,
)
from evalharness.scoring import Category, classify

TEST_FILE = "from solution import f\n\n\ndef test_f():\n    assert f() == 42\n"
LIMITS = SandboxLimits(timeout_s=4, memory_mb=128, pids=32)
K8S = DEFAULT_BACKEND == "kubernetes"
SA_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"  # noqa: S105 - a path


def run(solution: str, limits: SandboxLimits = LIMITS, expected_tests: int | None = 1):
    outcome = run_in_sandbox({"solution.py": solution, "test_solution.py": TEST_FILE}, limits)
    return classify(outcome, expected_tests), outcome


# ---- unit: the hardening flags are present (no Docker needed) ----


def test_docker_run_args_include_every_hardening_flag():
    args = docker_run_args("n", "img", SandboxLimits())
    joined = " ".join(args)
    for flag in [
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        f"--user {SANDBOX_UID}",
        "--memory 256m",
        "--memory-swap 256m",
        "--ipc none",
        f"--runtime {DEFAULT_RUNTIME}",
    ]:
        assert flag in joined, flag
    assert "noexec" in joined and "size=32m" in joined
    assert not any(a in ("-v", "--volume", "--mount", "--privileged", "-e", "--env") for a in args)


def test_runtime_flag_is_configurable():
    args = docker_run_args("n", "img", SandboxLimits(), runtime="runsc")
    assert args[args.index("--runtime") + 1] == "runsc"


def test_pids_limit_per_runtime():
    def pids(runtime):
        args = docker_run_args("n", "img", SandboxLimits(pids=64), runtime=runtime)
        return int(args[args.index("--pids-limit") + 1])

    assert pids("runc") == 64  # caps processes inside the container
    assert pids("runsc") == RUNSC_MIN_PIDS  # caps gVisor's own threads; needs headroom


# ---- docker: baseline ----

pytestmark_docker = pytest.mark.docker


@pytest.mark.docker
def test_correct_solution_passes():
    v, o = run("def f():\n    return 42\n")
    assert v.category == Category.PASSED
    if K8S:
        assert o.backend == "kubernetes" and o.runtime_used == "kubernetes"
    else:
        assert o.runtime_used == DEFAULT_RUNTIME  # Docker really used the runtime we asked for


@pytest.mark.docker
def test_sandbox_properties_from_inside(monkeypatch):
    """Run a probe in the sandbox and check what it can see."""
    monkeypatch.setenv("EVALHARNESS_FAKE_SECRET", "do-not-leak-me")
    host_path = str(Path(__file__).resolve())
    probe = f"""
import json, os
facts = {{}}
status = dict(l.split(":", 1) for l in open("/proc/self/status").read().splitlines() if ":" in l)
facts["uid"] = os.getuid(); facts["euid"] = os.geteuid()
facts["cap_eff"] = status["CapEff"].strip()
facts["no_new_privs"] = status["NoNewPrivs"].strip()
facts["seccomp"] = status["Seccomp"].strip()
facts["env_keys"] = sorted(os.environ)
facts["env_values"] = sorted(os.environ.values())
facts["net_ifaces"] = sorted(l.split(":")[0].strip() for l in open("/proc/net/dev").readlines()[2:])
facts["docker_sock"] = os.path.exists("/var/run/docker.sock")
facts["sa_token"] = os.path.exists({SA_TOKEN!r})
facts["k8s_env"] = [k for k in os.environ if k.startswith("KUBERNETES")]
facts["host_test_file_visible"] = os.path.exists({host_path!r})
try:
    open("/usr/evil", "w"); facts["rootfs_writable"] = True
except OSError as e:
    facts["rootfs_writable"] = False
facts["tmp_mount"] = [l for l in open("/proc/mounts") if " /tmp " in l]
facts["kernel"] = open("/proc/version").read().strip()
print("PROBE:" + json.dumps(facts))

def f():
    return 42
"""
    v, o = run(probe)
    assert v.category == Category.PASSED, v
    line = next(x for x in o.envelope["log_tail"].splitlines() if x.startswith("PROBE:"))
    facts = json.loads(line.removeprefix("PROBE:"))
    assert facts["uid"] == 10001 and facts["euid"] == 10001  # non-root
    assert int(facts["cap_eff"], 16) == 0  # no capabilities
    assert facts["no_new_privs"] == "1"  # can't gain privileges via setuid binaries
    host_kernel = Path("/proc/version").read_text().strip()
    if DEFAULT_RUNTIME == "runsc" and not K8S:
        # gVisor: the code talks to gVisor's user-space kernel, not the host's. (Docker's seccomp
        # profile is applied by gVisor internally and isn't shown in /proc; gVisor protects the
        # host with its own seccomp filter on the Sentry.)
        assert facts["kernel"] != host_kernel
    else:
        assert facts["kernel"] == host_kernel  # runc shares the host kernel...
        assert facts["seccomp"] == "2"  # ...so Docker's seccomp filter must be active
    assert "EVALHARNESS_FAKE_SECRET" not in facts["env_keys"]  # host env not passed in
    assert "do-not-leak-me" not in facts["env_values"]
    if K8S:
        # A pod always gets an interface; the deny-all NetworkPolicy drops its traffic
        # (checked by the network_connect violation test).
        assert facts["net_ifaces"] == ["eth0", "lo"]
    else:
        assert facts["net_ifaces"] == ["lo"]  # no network besides loopback
    assert facts["sa_token"] is False  # no Kubernetes API credential
    assert facts["k8s_env"] == []  # no service-discovery env vars
    assert facts["docker_sock"] is False  # can't drive Docker
    assert facts["host_test_file_visible"] is False  # host filesystem not mounted
    assert facts["rootfs_writable"] is False  # read-only root
    if not K8S:  # emptyDir can't be mounted noexec: a known gap of the k8s backend (D20)
        assert "noexec" in facts["tmp_mount"][0]


# ---- docker: escape-style behavior → sandbox_violation ----

VIOLATIONS = {
    "network_connect": "import socket\n"
    "def f():\n    socket.create_connection(('1.1.1.1', 53), timeout=2)\n    return 42\n",
    # The earliest moment untrusted code can act: on Kubernetes, this caught a window where a new
    # pod's traffic was allowed before the NetworkPolicy took effect (D20, egress gate).
    "network_connect_at_import": "import socket\n"
    "socket.create_connection(('1.1.1.1', 53), timeout=2)\n"
    "def f():\n    return 42\n",
    "dns_lookup": "import socket\n"
    "def f():\n    socket.getaddrinfo('example.com', 80)\n    return 42\n",
    "write_rootfs": "def f():\n    open('/etc/pwned', 'w').write('x')\n    return 42\n",
    "setuid_root": "import os\ndef f():\n    os.setuid(0)\n    return 42\n",
    "exit_before_tests": "import os\nos._exit(0)\ndef f():\n    return 42\n",
    "forge_envelope": "def f():\n"
    "    with open('/proc/1/fd/1', 'w') as out:\n"
    "        out.write('===EVALHARNESS-ENVELOPE-BEGIN:' + 'f'*32 + '===\\n{\"protocol\": 1}\\n')\n"
    "    return 42\n",
    "delete_tests": "import os\nos.remove('/tmp/work/test_solution.py')\ndef f():\n    return 0\n",
    "flood_host_stdout": "def f():\n"
    "    with open('/proc/1/fd/1', 'w') as out:\n"
    "        while True:\n            out.write('A' * 65536)\n",
}


@pytest.mark.docker
@pytest.mark.parametrize("name", sorted(VIOLATIONS))
def test_escape_attempts_are_sandbox_violations(name):
    v, _ = run(VIOLATIONS[name])
    assert v.category == Category.SANDBOX_VIOLATION, (name, v)


# ---- docker: infinite loops → timeout ----


@pytest.mark.docker
@pytest.mark.parametrize(
    "solution",
    [
        "def f():\n    while True:\n        pass\n",
        "while True:\n    pass\n\ndef f():\n    return 42\n",  # loops at import time
        "import time\ndef f():\n    time.sleep(3600)\n",
    ],
    ids=["loop_in_call", "loop_at_import", "sleep"],
)
def test_infinite_loops_time_out(solution):
    v, o = run(solution)
    assert v.category == Category.TIMEOUT, v
    assert o.duration_s < LIMITS.timeout_s + 10  # killed promptly, not left running


# ---- docker: excessive resource use → resource_limit ----

RESOURCE_HOGS = {
    "memory_bomb": "def f():\n    hog = []\n    while True:\n"
    "        hog.append(b'x' * (8 * 1024 * 1024))\n",
    "fork_bomb": "import os, time\ndef f():\n    while True:\n"
    "        if os.fork() == 0:\n            time.sleep(30)\n            os._exit(0)\n",
    "fill_tmpfs": "def f():\n    with open('/tmp/work/big', 'wb') as fh:\n"
    "        while True:\n            fh.write(b'x' * 1024 * 1024)\n",
}


@pytest.mark.docker
@pytest.mark.parametrize("name", sorted(RESOURCE_HOGS))
def test_resource_hogs_are_contained(name):
    v, o = run(RESOURCE_HOGS[name])
    assert v.category == Category.RESOURCE_LIMIT, (name, v)


# ---- docker: plain failures are categorized precisely ----


@pytest.mark.docker
@pytest.mark.parametrize(
    "solution,expected",
    [
        ("def f()\n    return 42\n", Category.SYNTAX_ERROR),
        ("def f():\n    return 41\n", Category.WRONG_ANSWER),
        ("def f():\n    return {}['missing']\n", Category.RUNTIME_ERROR),
        ("raise ImportError('nope')\n", Category.RUNTIME_ERROR),
        ("def g():\n    return 42\n", Category.RUNTIME_ERROR),  # wrong function name
    ],
    ids=["syntax", "wrong", "keyerror", "import_raises", "missing_entry_point"],
)
def test_ordinary_failures(solution, expected):
    assert run(solution)[0].category == expected


@pytest.mark.docker
def test_missing_image_is_harness_error():
    o = run_in_sandbox({"solution.py": "", "test_solution.py": TEST_FILE}, LIMITS, "no-such:img")
    assert classify(o).category == Category.HARNESS_ERROR


@pytest.mark.docker
@pytest.mark.skipif(K8S, reason="OCI runtimes are a Docker-backend option")
def test_unknown_runtime_is_harness_error():
    files = {"solution.py": "def f():\n    return 42\n", "test_solution.py": TEST_FILE}
    o = run_in_sandbox(files, LIMITS, runtime="no-such-runtime")
    v = classify(o)
    assert v.category == Category.HARNESS_ERROR, v


@pytest.mark.docker
def test_no_containers_left_behind():
    import subprocess

    run("def f():\n    while True:\n        pass\n")
    if K8S:
        cmd = ["kubectl", "get", "pods", "-A", "-l", "evalharness=sandbox", "-o", "name"]
    else:
        cmd = ["docker", "ps", "-aq", "--filter", "label=evalharness=sandbox"]
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ},
    )
    assert r.stdout.strip() == ""


@pytest.mark.docker
@pytest.mark.skipif(not K8S, reason="Pod Security admission is a Kubernetes feature")
def test_pod_security_rejects_a_weakened_pod(monkeypatch):
    """If a code change ever drops a hardening setting, the API server refuses the pod."""
    from evalharness import k8s_sandbox

    real = k8s_sandbox.pod_overrides

    def weakened(name, image, limits):
        spec = real(name, image, limits)
        spec["spec"]["securityContext"].update(runAsNonRoot=False, runAsUser=0)
        spec["spec"]["containers"][0]["securityContext"]["allowPrivilegeEscalation"] = True
        return spec

    monkeypatch.setattr(k8s_sandbox, "pod_overrides", weakened)
    files = {"solution.py": "def f():\n    return 42\n", "test_solution.py": TEST_FILE}
    o = run_in_sandbox(files, LIMITS)
    v = classify(o)
    assert v.category == Category.HARNESS_ERROR, v
    assert "PodSecurity" in (o.harness_error or ""), o.harness_error
