"""Every capability code a consumer reads must be in the entitlement vocabulary (KAN-38 / KAN-36).

`integration_entitlement.capability_code` is keyed by the eight lines of an auto-i-dat account sheet, seeded onto
the provider rows by migration 49be490e2077. Screens ask about *features* ("valuation", "vin_decode") instead;
`services/connections.py` describes the three layers.

`catalogue_entitlements.py` kept reading `images` / `packages` / `valuation` / `forecast` after that migration
replaced them, and the frontend kept asking `/integrations/capabilities/{packages,valuation}`. Nothing could ever
write those rows, and every test passed — the tests inserted the same dead codes by hand. A test that writes its
own fixture cannot tell you production never will. This one takes the vocabulary from the migration that seeds it
and the codes from the code that consumes them.

It checks membership, not that a writer exists today: `optionen`, `bewertung` and most other lines have no probe
yet, which the registry's optimistic default covers. A code outside the vocabulary is different — nothing that
writes entitlements can ever produce it. Only `tenant_has_capability` and the frontend ask about *features*; a
raw row read or write (`get_entitlement`, `record_probed_entitlement`, ...) is keyed by a sheet line.
"""

import ast
import importlib.util
import re
from pathlib import Path

from app.integration.services import connections as connection_service

_REPO = Path(__file__).resolve().parent.parent.parent
_APP = _REPO / "app"
_FRONTEND_SRC = _REPO / "frontend" / "apps" / "dms" / "src"
_VERSIONS = _REPO / "alembic" / "versions"

_CAPABILITY_URL = re.compile(r"/integrations/capabilities/([A-Za-z0-9_]+)")

# `vin_decode` is derived from the tenant's `dat` connection and cached as a row for display
# (`connections.compute_vin_decode_entitlement`); it is deliberately not the sheet lines `vin` / `vin_ident_db`.
DERIVED = {"vin_decode"}

# Read although no sheet line entitles them (`catalogue_entitlements`' docstring says why). Any other code that is
# not a sheet line, a derived code or a mapped feature fails the tests below.
KNOWN_UNMAPPED = {"images", "packages"}


def _sheet_lines() -> set[str]:
    """The eight counters as seeded onto `auto_i_dat` / `auto_i_dat_mock`. Tests build the schema with
    `create_all`, so — like the canonical-list tests — the vocabulary is read from the migration by path.
    """

    (path,) = (_VERSIONS / "integration").glob("49be490e2077_*.py")
    spec = importlib.util.spec_from_file_location("kan36_account_shape", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module._AUTO_I_DAT_CAPABILITY_CODES)


def _row_codes() -> set[str]:
    """What an `integration_entitlement` row can be keyed by."""

    return _sheet_lines() | DERIVED | KNOWN_UNMAPPED


def _askable_codes() -> set[str]:
    """What a screen may ask about: a row code, or a feature `tenant_has_capability` translates."""

    return _row_codes() | set(connection_service.FEATURE_SHEET_LINES)


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    return {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _callee(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    return node.func.attr if isinstance(node.func, ast.Attribute) else ""


def _codes_used_by_the_backend() -> list[tuple[str, int, str, str]]:
    """(file, line, callee, code) for every `capability_code=` keyword (a literal, or a module-level string
    constant) and every `CapabilityProbe("...")` first argument in `app/`. Call labels go through `capability=`,
    a different layer, so they are correctly not collected.
    """

    found: list[tuple[str, int, str, str]] = []
    for path in sorted(_APP.rglob("*.py")):
        tree = ast.parse(path.read_text())
        constants = _module_string_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            values = [kw.value for kw in node.keywords if kw.arg == "capability_code"]
            if _callee(node) == "CapabilityProbe" and node.args:
                values.append(node.args[0])
            for value in values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    code = value.value
                elif isinstance(value, ast.Name) and value.id in constants:
                    code = constants[value.id]
                else:
                    continue
                found.append((str(path.relative_to(_REPO)), node.lineno, _callee(node), code))
    return found


def test_every_code_the_backend_reads_or_writes_is_in_the_vocabulary():
    found = _codes_used_by_the_backend()
    # The scanner must see the forms it claims to (a module constant, a probe's first argument) — otherwise a
    # broken scan would pass by finding nothing.
    assert {"fahrzeuge", "vin_decode"} <= {code for _, _, _, code in found}

    strays = [
        f"{path}:{line} {callee}() uses {code!r}"
        for path, line, callee, code in found
        if code not in (_askable_codes() if callee == "tenant_has_capability" else _row_codes())
    ]
    assert not strays, (
        "capability code(s) outside the vocabulary — nothing that writes entitlements can ever produce them, so a "
        "read of one is silently always 'granted'. Use a sheet line (raw rows are keyed by those only), or ask "
        "`tenant_has_capability` about a feature mapped in connections.FEATURE_SHEET_LINES:\n  " + "\n  ".join(strays)
    )


def test_every_code_the_frontend_asks_about_is_in_the_vocabulary():
    found = [
        (str(path.relative_to(_REPO)), match.group(1))
        for path in sorted(_FRONTEND_SRC.rglob("*.ts*"))
        for match in _CAPABILITY_URL.finditer(path.read_text())
    ]
    assert found, f"no /integrations/capabilities/<code> call found under {_FRONTEND_SRC} — did the frontend move?"

    strays = [f"{path} asks about {code!r}" for path, code in found if code not in _askable_codes()]
    assert not strays, (
        "the frontend asks about capability codes outside the vocabulary; the endpoint answers 'granted' for any "
        "string, so the banner it drives can never appear:\n  " + "\n  ".join(strays)
    )


def test_every_feature_follows_a_seeded_sheet_line():
    for feature, line in connection_service.FEATURE_SHEET_LINES.items():
        assert line in _sheet_lines(), f"feature {feature!r} follows {line!r}, which no provider declares"
