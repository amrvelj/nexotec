"""KAN-731: `pr-light` (pr.yml) runs the rule-glob guard (KAN-721) on every PR, whatever
the diff, without the suite's conftest, and even after an earlier step failed. In
`scripts/dev/check` the guard sits in the backend lane, which a rule-only or frontend-only
diff does not run; pinned here so a workflow edit cannot drop it silently.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _workflow(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def _runs(job: dict) -> list[dict]:
    return [step for step in job["steps"] if "run" in step]


GUARD = "tests/architecture/test_rule_globs_match_a_file.py"


def test_pr_light_runs_the_rule_glob_guard_on_every_pr():
    workflow = _workflow("pr.yml")
    steps = [step for step in _runs(workflow["jobs"]["pr-light"]) if GUARD in step["run"]]

    assert len(steps) == 1, "pr-light must run the rule-glob guard (KAN-731)"
    (step,) = steps
    assert step["run"].split()[:3] == ["python", "-m", "pytest"]
    # No conftest: pr-light has neither a database nor the app's settings.
    assert "--noconftest" in step["run"].split()
    # Shown even when ruff or the secret scan failed earlier in the job.
    assert step.get("if") == "${{ !cancelled() }}"
    # pr.yml's `on` is the one trigger list: no path filter may narrow it.
    triggers = workflow.get("on", workflow.get(True))
    assert "paths" not in triggers["pull_request"] and "paths-ignore" not in triggers["pull_request"]


def test_the_guard_needs_no_conftest():
    """The guard imports nothing of `app`, so --noconftest cannot hide an import error."""

    source = (ROOT / GUARD).read_text(encoding="utf-8")
    assert "import app" not in source and "from app" not in source
