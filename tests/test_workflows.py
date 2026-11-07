"""Guards on .github/workflows: the deploy workflow stays off by default, and nothing in CI
can be quietly swapped out (every third-party action pinned to a commit)."""

import re
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _load(name: str) -> dict:
    doc = yaml.safe_load((WORKFLOWS / name).read_text())
    # YAML 1.1 reads the key `on` as the boolean True.
    doc["on"] = doc.pop(True, doc.get("on"))
    return doc


def test_every_action_is_pinned_to_a_commit_sha():
    for path in WORKFLOWS.glob("*.yml"):
        for line in path.read_text().splitlines():
            m = re.search(r"uses:\s*([^\s#]+)", line)
            if m:
                ref = m.group(1).split("@")
                assert len(ref) == 2 and re.fullmatch(r"[0-9a-f]{40}", ref[1]), (path.name, line)


def test_deploy_never_runs_on_its_own():
    wf = _load("deploy.yml")
    assert set(wf["on"]) == {"workflow_dispatch"}, "deploy must be manual only"
    assert wf["on"]["workflow_dispatch"]["inputs"]["action"]["default"] == "plan"
    assert wf["permissions"] == {}  # nothing by default; jobs ask for what they need
    for name, job in wf["jobs"].items():
        assert "vars.EVALHARNESS_DEPLOY_ENABLED == 'true'" in job["if"], name
        assert job["environment"] == "azure", name  # approval gate lives on the environment


def test_deploy_uses_oidc_not_stored_secrets():
    text = (WORKFLOWS / "deploy.yml").read_text()
    assert "secrets." not in text
    assert 'ARM_USE_OIDC: "true"' in text
    for job in _load("deploy.yml")["jobs"].values():
        assert job["permissions"].get("id-token") == "write"


def test_ci_has_no_secrets_and_read_only_token():
    text = (WORKFLOWS / "ci.yml").read_text()
    assert "secrets." not in text
    assert _load("ci.yml")["permissions"] == {"contents": "read"}


def test_ci_checks_terraform_without_a_backend():
    steps = _load("ci.yml")["jobs"]["terraform"]["steps"]
    runs = " ".join(s.get("run", "") for s in steps)
    assert "terraform fmt -check" in runs and "terraform validate" in runs
    assert "-backend=false" in runs
    assert "apply" not in runs and "plan" not in runs  # static checks only
