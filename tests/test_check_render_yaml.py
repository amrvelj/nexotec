"""KAN-236: render.yaml is validated against Render's Blueprint schema.

scripts/dev/check chose lanes by path and nothing covered render.yaml, so a change
to it recorded a PASS without running anything. These tests pin the validator that
the `deploy` lane and CI's mypy job run: the checked-in file passes, a plan Render
does not know fails and names the field, a misspelt service key is named rather
than drowned in "not valid under any of the given schemas", an unknown top-level
key is still reported, a file that is not YAML fails with a message that says so,
`main()` returns the exit code the lane relies on, and the vendored schema matches
its provenance sidecar. KAN-281: a service `type` Render does not know is one line
naming every allowed type, a misspelt key inside an `envVars` entry is named, and
CI's step reports even when mypy before it fails. No network: the schema is the copy under scripts/dev/.
"""

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from scripts import check_render_yaml

ROOT = Path(__file__).resolve().parents[1]


def _blueprint_with(tmp_path: Path, old: str, new: str) -> Path:
    source = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert old in source, f"the fixture relies on {old!r} being in render.yaml"
    path = tmp_path / "render.yaml"
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return path


def test_checked_in_render_yaml_validates() -> None:
    assert check_render_yaml.validate(ROOT / "render.yaml") == []


def test_a_plan_render_does_not_know_fails_and_is_named(tmp_path: Path) -> None:
    problems = check_render_yaml.validate(_blueprint_with(tmp_path, "plan: 0.1c-256mb", "plan: bogus-plan"))
    assert len(problems) == 1, problems
    assert "databases/0/plan" in problems[0] and "bogus-plan" in problems[0]


def test_a_misspelt_service_key_is_named(tmp_path: Path) -> None:
    problems = check_render_yaml.validate(_blueprint_with(tmp_path, "    plan: free\n", "    planz: free\n"))
    assert problems, "a key Render does not know must fail validation"
    assert any("services/0" in p and "planz" in p for p in problems), problems
    assert not any("not valid under any of the given schemas" in p for p in problems), problems


def test_an_unknown_top_level_key_is_still_reported(tmp_path: Path) -> None:
    problems = check_render_yaml.validate(_blueprint_with(tmp_path, "services:\n", "servicez:\n"))
    assert any("servicez" in p for p in problems), problems


def test_a_service_type_render_does_not_know_is_one_line_naming_every_allowed_type(tmp_path: Path) -> None:
    problems = check_render_yaml.validate(_blueprint_with(tmp_path, "    type: web\n", "    type: webz\n"))
    assert len(problems) == 1, problems
    assert "services/0/type" in problems[0] and "'webz'" in problems[0], problems
    assert "allowed: cron, keyvalue, pserv, redis, web, worker, workflow" in problems[0], problems


def test_a_misspelt_key_in_an_env_var_entry_is_named(tmp_path: Path) -> None:
    blueprint = _blueprint_with(tmp_path, "      - key: PYTHON_VERSION\n", "      - keyz: PYTHON_VERSION\n")
    problems = check_render_yaml.validate(blueprint)
    assert any("services/0/envVars/0" in p and "'keyz' was unexpected" in p for p in problems), problems
    assert not any("not valid under any of the given schemas" in p for p in problems), problems


def test_an_error_inside_from_database_is_named_where_it_is(tmp_path: Path) -> None:
    # A tie between envVars branches went to the first one, which called the correct
    # `fromDatabase` key unexpected (KAN-281 review); the deepest complaint wins now.
    blueprint = _blueprint_with(tmp_path, "property: connectionString", "property: connstr")
    problems = check_render_yaml.validate(blueprint)
    assert any("fromDatabase/property" in p and "'connstr'" in p for p in problems), problems
    assert not any("'fromDatabase' was unexpected" in p for p in problems), problems


def test_a_valid_kind_is_explained_by_its_own_branch(tmp_path: Path) -> None:
    # `type: cron` without a schedule: the cron branch speaks, never another kind's
    # "'workflow' was expected" (the closest branch used to win even when it rejected the kind).
    problems = check_render_yaml.validate(_blueprint_with(tmp_path, "    type: web\n", "    type: cron\n"))
    assert any("'schedule' is a required property" in p for p in problems), problems
    assert not any(p.split(": ")[1].endswith("/type") for p in problems), problems


def test_ci_runs_the_check_even_when_mypy_before_it_fails() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8"))
    steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
    [step] = [step for step in steps if step.get("run") == "python scripts/check_render_yaml.py"]
    assert step.get("if") == "${{ !cancelled() }}"


def test_a_file_that_is_not_yaml_fails_with_a_yaml_message(tmp_path: Path) -> None:
    broken = tmp_path / "render.yaml"
    broken.write_text("services:\n  - name: x\n   type: web\n", encoding="utf-8")  # mixed indentation
    problems = check_render_yaml.validate(broken)
    assert len(problems) == 1 and "not valid YAML" in problems[0], problems


def test_a_scalar_document_fails_without_a_traceback(tmp_path: Path) -> None:
    broken = tmp_path / "render.yaml"
    broken.write_text("just a string\n", encoding="utf-8")
    assert check_render_yaml.validate(broken) == [f"{broken}: expected a mapping at the top level, got str"]


def test_main_exit_codes_are_what_the_lane_relies_on(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    broken = _blueprint_with(tmp_path, "plan: 0.1c-256mb", "plan: bogus-plan")
    assert check_render_yaml.main([str(broken)]) == 1
    assert "bogus-plan" in capsys.readouterr().out
    assert check_render_yaml.main([]) == 0
    assert "valid against" in capsys.readouterr().out


def test_vendored_schema_matches_its_sidecar() -> None:
    meta = json.loads((ROOT / "scripts" / "dev" / "render.yaml.schema.meta.json").read_text(encoding="utf-8"))
    raw = (ROOT / "scripts" / "dev" / "render.yaml.schema.json").read_bytes()
    assert meta["source"] == check_render_yaml.SCHEMA_URL
    assert hashlib.sha256(raw).hexdigest() == meta["sha256"], "schema and sidecar were edited apart"
    assert json.loads(raw)["$id"] == check_render_yaml.SCHEMA_URL


# --- KAN-604: a service that runs the app declares the secrets manager's keys ---


@pytest.mark.parametrize("key", check_render_yaml.REQUIRED_APP_ENV_KEYS)
def test_a_service_running_the_app_without_an_infisical_key_fails_and_names_it(tmp_path: Path, key: str) -> None:
    broken = _blueprint_with(tmp_path, f"      - key: {key}\n", "      - key: DMS_UNRELATED_PLACEHOLDER\n")
    problems = check_render_yaml.validate(broken)
    assert len(problems) == 1, problems
    assert "dms-staging" in problems[0]
    assert key in problems[0]
    assert check_render_yaml.main([str(broken)]) == 1


def test_the_checked_in_staging_service_declares_every_infisical_key_unsynced() -> None:
    document = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    (staging,) = [s for s in document["services"] if s["name"] == "dms-staging"]
    entries = {e["key"]: e for e in staging["envVars"]}
    for key in (*check_render_yaml.REQUIRED_APP_ENV_KEYS, "DMS_INFISICAL_ENVIRONMENT"):
        assert entries[key] == {"key": key, "sync": False}, f"{key}: declared without a value, pasted in Render"


def test_a_service_that_does_not_run_the_app_needs_no_infisical_key() -> None:
    document = {"services": [{"name": "static-site", "type": "web", "envVars": [{"key": "NODE_VERSION", "value": "22"}]}]}
    assert check_render_yaml.missing_required_env(document) == []
