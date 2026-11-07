"""Static checks on deploy/k8s: every pod we ship is locked down, and every image is pinned.

These run without a cluster. The cluster itself also enforces the "restricted" Pod Security
Standard on both namespaces; these tests catch mistakes before anything is applied.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
K8S = ROOT / "deploy" / "k8s"


def _docs():
    for path in sorted(K8S.glob("*.yaml")):
        if path.name == "kustomization.yaml":
            continue
        for doc in yaml.safe_load_all(path.read_text()):
            if doc:
                yield path.name, doc


def _pod_specs():
    for name, doc in _docs():
        if doc["kind"] in ("Deployment", "Job"):
            yield f"{name}:{doc['kind']}", doc["spec"]["template"]["spec"]


POD_SPECS = dict(_pod_specs())


def test_found_the_workloads():
    assert len(POD_SPECS) == 4  # API, Prometheus, Grafana, evaluator Job


@pytest.mark.parametrize("where", sorted(POD_SPECS))
def test_every_pod_is_locked_down(where):
    spec = POD_SPECS[where]
    psc = spec["securityContext"]
    assert psc["runAsNonRoot"] is True and psc["runAsUser"] != 0
    assert psc["seccompProfile"] == {"type": "RuntimeDefault"}
    assert spec.get("enableServiceLinks") is False
    if spec.get("serviceAccountName") != "evaluator":  # only the evaluator talks to the API
        assert spec.get("automountServiceAccountToken") is False
    for c in spec["containers"]:
        sc = c["securityContext"]
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["readOnlyRootFilesystem"] is True
        assert sc["capabilities"] == {"drop": ["ALL"]}
        assert "limits" in c["resources"], "every container needs CPU/memory limits"
        image = c["image"]
        if c.get("imagePullPolicy") == "Never":
            assert image.startswith("evalharness-")  # built here, loaded into kind
        else:
            assert "@sha256:" in image, f"third-party image not pinned by digest: {image}"
    for v in spec.get("volumes", []):
        assert "hostPath" not in v and "secret" not in v


def test_sandbox_namespace_is_restricted_and_denies_all_traffic():
    docs = [d for _, d in _docs()]
    ns = next(
        d for d in docs if d["kind"] == "Namespace" and d["metadata"]["name"].endswith("sandbox")
    )
    assert ns["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"
    deny = next(
        d for d in docs
        if d["kind"] == "NetworkPolicy" and d["metadata"]["namespace"] == "evalharness-sandbox"
    )  # fmt: skip
    assert deny["spec"]["podSelector"] == {}
    assert set(deny["spec"]["policyTypes"]) == {"Ingress", "Egress"}
    assert "ingress" not in deny["spec"] and "egress" not in deny["spec"]


def test_evaluator_rbac_is_narrow():
    roles = [d for _, d in _docs() if d["kind"] in ("Role", "ClusterRole")]
    assert all(r["kind"] == "Role" for r in roles), "no cluster-wide permissions"
    for r in roles:
        for rule in r["rules"]:
            assert "secrets" not in rule["resources"]
            assert "*" not in rule["verbs"] and "*" not in rule["resources"]


def test_grafana_has_no_accounts():
    grafana = next(s for w, s in POD_SPECS.items() if "grafana" in w)
    env = {e["name"]: e["value"] for e in grafana["containers"][0]["env"]}
    assert env["GF_AUTH_ANONYMOUS_ORG_ROLE"] == "Viewer"
    assert env["GF_AUTH_DISABLE_LOGIN_FORM"] == "true"
    assert env["GF_AUTH_BASIC_ENABLED"] == "false"
    assert env["GF_SECURITY_DISABLE_INITIAL_ADMIN_CREATION"] == "true"
    assert not any("PASSWORD" in k for k in env)


def test_dashboard_is_generated_and_labels_mock_data(tmp_path):
    committed = (K8S / "grafana" / "evalharness.json").read_text()
    subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_dashboard.py")], check=True,
                   capture_output=True)  # fmt: skip
    assert (K8S / "grafana" / "evalharness.json").read_text() == committed, (
        "dashboard JSON is stale: run scripts/gen_dashboard.py"
    )
    d = json.loads(committed)
    assert "MOCK" in d["title"]
    exprs = [t["expr"] for p in d["panels"] for t in p.get("targets", [])]
    # Any panel that shows a pass rate also queries its interval and sample size.
    for p in d["panels"]:
        e = " ".join(t["expr"] for t in p.get("targets", []))
        if "evalharness_pass_rate" in e:
            assert "ci_low" in e and "ci_high" in e, p["title"]
    assert any("is_mock" in x for x in exprs)


@pytest.mark.skipif(not shutil.which("kubectl"), reason="needs kubectl to render the overlay")
def test_aks_overlay_keeps_every_lockdown_setting():
    out = subprocess.run(
        ["kubectl", "kustomize", str(ROOT / "deploy" / "aks")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    docs = [d for d in yaml.safe_load_all(out) if d]
    pods = {
        d["metadata"]["name"]: d["spec"]["template"]["spec"]
        for d in docs
        if d["kind"] == "Deployment"
    }
    assert set(pods) == {"evalharness-service", "prometheus", "grafana"}
    for spec in pods.values():
        assert spec["securityContext"]["runAsNonRoot"] is True
        for c in spec["containers"]:
            assert c["securityContext"]["readOnlyRootFilesystem"] is True
            assert c["securityContext"]["capabilities"] == {"drop": ["ALL"]}
            assert c["imagePullPolicy"] != "Never"  # AKS pulls from the registry
    cfg = next(
        d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "evaluator-config"
    )
    assert cfg["data"]["EVALHARNESS_SANDBOX_NODE_POOL"] == "sandbox"
    assert cfg["data"]["EVALHARNESS_SANDBOX_RUNTIME_CLASS"]
    sandbox_ns = next(
        d
        for d in docs
        if d["kind"] == "Namespace" and d["metadata"]["name"] == "evalharness-sandbox"
    )
    assert sandbox_ns["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"
