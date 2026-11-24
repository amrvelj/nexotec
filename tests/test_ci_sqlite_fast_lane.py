"""KAN-661: the nightly run (test.yml) runs the full SQLite fast lane without a test
database URL, and a red fast lane opens the nightly-red issue like any other job. It never
gates a merge (ADR-011): no pull-request or push workflow runs it. Pinned so a workflow
edit cannot drop it silently.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _workflow(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def _runs(job: dict) -> list[dict]:
    return [step for step in job["steps"] if "run" in step]


def test_the_nightly_run_runs_the_full_sqlite_fast_lane():
    jobs = _workflow("test.yml")["jobs"]
    job = jobs["sqlite-fast-lane"]

    runs = [step["run"] for step in _runs(job) if "pytest" in step["run"]]
    assert runs == ["env -u DMS_TEST_DATABASE_URL python -m pytest -n auto"]
    # SQLite only: no Postgres service, no test database named anywhere in the job.
    assert "services" not in job
    assert "DMS_TEST_DATABASE_URL" not in str(job.get("env", {}))
    assert all("DMS_TEST_DATABASE_URL" not in str(step.get("env", {})) for step in job["steps"])
    # Skipped with the rest of a night that is already green, and a red lane is alerted.
    assert job["needs"] == "freshness"
    assert "sqlite-fast-lane" in jobs["alert"]["needs"]


def test_no_pull_request_workflow_runs_the_sqlite_lane():
    """ADR-011: the fast lane never gates a merge."""

    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        triggers = workflow.get("on", workflow.get(True)) or {}
        if "pull_request" not in triggers and "push" not in triggers:
            continue
        assert "-u DMS_TEST_DATABASE_URL" not in path.read_text(encoding="utf-8"), path.name
