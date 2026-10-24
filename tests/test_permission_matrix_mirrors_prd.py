"""KAN-198 (A-RULE-6): the capability registry and the Dealer Administration
PRD's permission matrix agree, row for row.

tests/fixtures/permission_matrix.json is a transcription of the Notion table
(read 2026-10-17). A row added to Notion without a registry entry, a registry
key without a row, or a read/write set that differs fails here — the
registry is generated from the matrix, never the other way round.

A row may carry `enforced_write` + `deviation` (a ticket) where the code
deliberately enforces less than the matrix grants: the registry is held to
`enforced_write`, the matrix value stays in `write` so the difference is
visible, and a deviation that no longer differs fails as stale.
"""

import json
from pathlib import Path

from app.core.auth import _ALL_MODULE_CODES, AccessRole
from app.core.permissions import ALL_MODULES, CAPABILITY_MATRIX, ModuleCode

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "permission_matrix.json"


def _rows() -> list[dict]:
    return json.loads(_FIXTURE.read_text())["rows"]


def _roles(names: list[str] | None) -> frozenset[AccessRole] | None:
    if names is None:
        return None
    return frozenset(AccessRole(name) for name in names)


def _keys(row: dict) -> list[str]:
    key = row["key"]
    return key if isinstance(key, list) else [key]


def test_every_matrix_row_has_a_registry_entry_with_the_same_sets():
    failures = []
    for row in _rows():
        for key in _keys(row):
            cap = CAPABILITY_MATRIX.get(key)
            if cap is None:
                failures.append(f"{row['label']!r}: no CAPABILITY_MATRIX[{key!r}]")
                continue
            expected_read = _roles(row["read"])
            expected_write = _roles(row.get("enforced_write", row["write"]))
            if cap.read_roles != expected_read:
                failures.append(f"{key}: read {sorted(r.value for r in cap.read_roles or [])} != matrix {row['read']}")
            if cap.write_roles != expected_write:
                failures.append(f"{key}: write {sorted(r.value for r in cap.write_roles)} != matrix {row['write']}")
            if cap.manager_can_write != row.get("manager_can_write", True):
                failures.append(f"{key}: manager_can_write {cap.manager_can_write} != matrix")
            if cap.manager_can_read != row.get("manager_can_read", True):
                failures.append(f"{key}: manager_can_read {cap.manager_can_read} != matrix")
    assert failures == [], "\n".join(failures)


def test_every_deviation_from_the_matrix_names_a_ticket_and_still_differs():
    deviating = [row for row in _rows() if "enforced_write" in row or "deviation" in row]
    for row in deviating:
        assert "enforced_write" in row and "deviation" in row, row["label"]
        assert row["deviation"].startswith("KAN-"), row["label"]
        assert set(row["enforced_write"]) < set(row["write"]), f"{row['label']}: deviation is stale or widens"
    # KAN-376 closed the last one (stock_items: the inventory write endpoints
    # now depend on their own rows, so the matrix's sales write is enforced).
    assert [row["key"] for row in deviating] == []


def test_every_registry_key_is_a_matrix_row():
    in_fixture = {key for row in _rows() for key in _keys(row)}
    orphans = sorted(set(CAPABILITY_MATRIX) - in_fixture)
    assert orphans == [], f"capabilities without a matrix row (A-RULE-6: not shippable): {orphans}"


def test_the_fixture_has_no_duplicate_keys():
    keys = [key for row in _rows() for key in _keys(row)]
    assert len(keys) == len(set(keys))


def test_module_vocabulary_is_one_set_in_auth_and_permissions():
    # app.core.auth cannot import permissions (permissions imports auth), so it
    # carries a frozen copy for create_access_token's "every module" default.
    assert _ALL_MODULE_CODES == ALL_MODULES == frozenset(m.value for m in ModuleCode)
    assert ALL_MODULES == {"sales", "aftersales", "parts", "finance", "reporting"}
