"""A refusal reason raised as a value is read and translated too (KAN-801).

The twin scans (`test_frontend_knows_backend_refusals.py`, `apiRefusal.test.ts`)
read `"reason": "literal"` and `"reason": CONSTANT`. A reason that arrives as an
expression — `_refuse(reason, message)` with `"reason": reason`, `refusal.code`,
an f-string over an enum — was read by neither, so one added untranslated
reached the dealer as the screen's fallback and no test failed.

`tests/refusal_reason_values.py` reads those sites; `refusal_reason_value_sites.json`
pins each with its value set (or as not a refusal, and why). These tests keep
the JSON equal to the code, and hold every value to the SPA's tables: a
refusal's to `REASON_KEYS` (or `notARefusalReasons.json`), a reassignment
problem's to `userOptions.ts::PROBLEM_REASONS`. The vitest twin reads the
JSON's values, so a sentence removed on the frontend side fails there too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.refusal_reason_values import Resolver, Unresolved, read_sites_json
from tests.test_frontend_knows_backend_refusals import DMS_SRC, not_a_refusal, translated_reasons

USER_OPTIONS_TS = DMS_SRC / "userOptions.ts"
KINDS = {"refusal", "problem", "notARefusal"}


@pytest.fixture(scope="module")
def resolver() -> Resolver:
    return Resolver()


def problem_reasons() -> set[str]:
    source = USER_OPTIONS_TS.read_text(encoding="utf-8")
    table = re.search(r"const PROBLEM_REASONS = new Set\(\[([^\]]*)\]\)", source)
    assert table, "userOptions.ts no longer declares PROBLEM_REASONS as this test reads it"
    return set(re.findall(r"'([a-z0-9_]+)'", table.group(1)))


def drift(resolver: Resolver, listed: dict[str, dict]) -> list[str]:
    """What differs between the code and the pinned list, one line each."""

    problems: list[str] = []
    found = {site.key: site for site in resolver.sites()}
    problems += [f"not listed: {key}" for key in sorted(found.keys() - listed.keys())]
    problems += [f"no longer in app/: {key}" for key in sorted(listed.keys() - found.keys())]
    for key in sorted(found.keys() & listed.keys()):
        entry = listed[key]
        if entry.get("kind") not in KINDS:
            problems.append(f"{key}: kind must be one of {sorted(KINDS)}")
            continue
        if entry["kind"] == "notARefusal":
            if not entry.get("why") or "values" in entry:
                problems.append(f"{key}: a notARefusal entry says why and lists no values")
            continue
        try:
            read = resolver.values(found[key], entry.get("hints"))
        except Unresolved as exc:
            problems.append(f"{key}: the scan cannot read it ({exc}) — give it a hint, or list it as notARefusal")
            continue
        if sorted(read) != entry.get("values"):
            problems.append(f"{key}: the code raises {sorted(read)}, the list says {entry.get('values')}")
    return problems


def unsaid(listed: dict[str, dict], translated: set[str], exempt: set[str], problems: set[str]) -> list[str]:
    missing: list[str] = []
    for key, entry in sorted(listed.items()):
        if entry.get("kind") == "refusal":
            missing += [f"{key}: {value}" for value in entry["values"] if value not in translated | exempt]
        elif entry.get("kind") == "problem":
            missing += [f"{key}: {value}" for value in entry["values"] if value not in problems]
    return missing


def test_the_pinned_list_is_the_code(resolver):
    assert drift(resolver, read_sites_json()) == [], (
        'tests/refusal_reason_value_sites.json differs from app/ — a new `"reason": <expression>` is listed with '
        "the values tests/refusal_reason_values.py reads (kind refusal or problem), or as notARefusal with why"
    )


def test_the_scan_reads_the_known_forms(resolver):
    listed = read_sites_json()
    values = {key: set(entry.get("values", [])) for key, entry in listed.items()}
    # a helper's parameter, followed to the literal its callers pass
    assert "unknown_field" in values["app/core/list_query.py::_refuse::reason"]
    # a pass-through chain (reason=reason) and a default
    assert values["app/platform/identity/services/user.py::_assert_not_last_manager::reason"] == {
        "last_manager",
        "last_manager_elsewhere",
    }
    # a local picked by a branch
    assert (
        "use_offboarding_procedure"
        in values["app/platform/organisation/services/dealership.py::_assert_status_change::reason"]
    )
    # hints: an exception's code, a dataclass's code, an enum in an f-string
    assert "not_an_administrator" in values["app/integration/services/mail_link.py::_refusal::reason"]
    assert values["app/sales/services/ownership.py::_assert_colleague_acts_everywhere::refusal.code"] == {
        "colleague_cannot_act_there"
    }
    assert values[
        "app/platform/administration/services/data_export.py::_assert_downloadable::f'export_{job.status.value}'"
    ] == {"export_requested", "export_failed"}


def test_the_spa_says_every_reason_raised_as_a_value():
    assert unsaid(read_sites_json(), translated_reasons(), not_a_refusal(), problem_reasons()) == [], (
        "a reason raised as a value that the SPA cannot say: a refusal's goes into REASON_KEYS (apiRefusal.ts) with "
        "its four sentences, a reassignment problem's into userOptions.ts PROBLEM_REASONS with usersAdmin.deactivate.problems"
    )


def _probe_app(root: Path) -> Path:
    app = root / "app"
    module = app / "inventory" / "services" / "new_rule.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "def _refuse(reason: str, message: str):\n"
        '    return ConflictError(message, details={"reason": reason})\n'
        "\n"
        'def _guard(code: str = "kan_801_default"):\n'
        '    raise _refuse(code, "x")\n'
        "\n"
        "def book():\n"
        '    raise _refuse("kan_801_probe", "x")\n'
        "\n"
        "def other():\n"
        "    _guard()\n"
        '    _guard(code="kan_801_passed")\n'
        "\n"
        "def opaque(refusal):\n"
        '    raise ConflictError("x", details={"reason": refusal.code})\n',
        encoding="utf-8",
    )
    return app


def test_an_untranslated_reason_raised_through_a_helper_fails(tmp_path):
    """The reproduction of KAN-801: a helper `_refuse(reason, …)` raising a new,
    untranslated reason. Before, neither scan read it; now the list must name
    the site, and its value must be said."""

    probe = Resolver(_probe_app(tmp_path))
    site = next(s for s in probe.sites() if s.function == "_refuse")
    assert probe.values(site) == {"kan_801_probe", "kan_801_default", "kan_801_passed"}

    # Unlisted, the list is out of date; an opaque form is not guessed.
    assert drift(probe, {}) == [
        "not listed: app/inventory/services/new_rule.py::_refuse::reason",
        "not listed: app/inventory/services/new_rule.py::opaque::refusal.code",
    ]
    with pytest.raises(Unresolved):
        probe.values(next(s for s in probe.sites() if s.function == "opaque"))

    # Listed with the values it reads, the untranslated one is named.
    listed = {
        "app/inventory/services/new_rule.py::_refuse::reason": {
            "kind": "refusal",
            "values": ["kan_801_default", "kan_801_passed", "kan_801_probe"],
        },
        "app/inventory/services/new_rule.py::opaque::refusal.code": {"kind": "notARefusal", "why": "probe"},
    }
    assert drift(probe, listed) == []
    assert unsaid(listed, translated_reasons(), not_a_refusal(), problem_reasons()) == [
        "app/inventory/services/new_rule.py::_refuse::reason: kan_801_default",
        "app/inventory/services/new_rule.py::_refuse::reason: kan_801_passed",
        "app/inventory/services/new_rule.py::_refuse::reason: kan_801_probe",
    ]

    # A value listed by hand that the code does not raise is drift too.
    listed["app/inventory/services/new_rule.py::_refuse::reason"]["values"] = ["kan_801_probe"]
    assert drift(probe, listed) == [
        (
            "app/inventory/services/new_rule.py::_refuse::reason: the code raises "
            "['kan_801_default', 'kan_801_passed', 'kan_801_probe'], the list says ['kan_801_probe']"
        )
    ]
