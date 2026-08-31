"""Architecture test (WP-8 PR-4, ADR-057/S-D10): "There is no `vatTreatment`.
No field, no enum, no column, no badge, no selector, no presentation
switch — not in the model, not in the API, not in the UI." An offer, a
contract and an invoice carry ONE price: gross, CHF incl. MwSt, after all
discounts. VAT is a single line on the printed document only (PR-7),
computed at the dealer-configurable `dealer_settings.vat_rate` — never a
per-vehicle or per-deal attribute.

Mirrors app/inventory's own precedent
(test_inventory_purchase.py::test_no_vat_treatment_field_exists_anywhere,
WP-7 PR-3) but as a whole-repo scan rather than a per-schema field check,
since Sales spans backend models/schemas AND frontend types/components —
one test covering both, rather than duplicating the schema-field check
per module and hoping every future frontend type stays in sync by hand.

The pattern requires a colon or `=` right after the token — a type
annotation or an assignment — so it does NOT flag the many existing
comments that explain the absence in prose ("there is NO vatTreatment
field anywhere"), only an actual declaration or assignment. Case-
insensitive and tolerant of both snake_case and camelCase spellings, and
of the JSON-string form a frontend type or i18n key would take
("vatTreatment":).
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_ROOTS = [
    _REPO_ROOT / "app",
    _REPO_ROOT / "frontend" / "apps" / "dms" / "src",
    _REPO_ROOT / "frontend" / "packages" / "ui-kit" / "src",
]
_SCAN_SUFFIXES = {".py", ".ts", ".tsx"}

# Matches `vat_treatment:`, `vat_treatment =`, `vatTreatment:`,
# `vatTreatment =`, `"vatTreatment":` — a declaration or assignment, never
# bare prose mentioning the term.
_PATTERN = re.compile(r"[\"']?vat[_]?treatment[\"']?\s*[:=]", re.IGNORECASE)


def test_no_vat_treatment_field_or_assignment_anywhere():
    violations = []
    for root in _SCAN_ROOTS:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in _SCAN_SUFFIXES:
                continue
            if "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                # A comment/docstring line explaining the absence in prose
                # ("there is NO vatTreatment field") never has a colon or
                # `=` directly after the token, so the pattern above
                # already excludes it — this second guard only catches the
                # rare case of a comment that happens to end in a colon
                # right after the word (e.g. a heading).
                if stripped.startswith(("#", "*", "//")):
                    continue
                if _PATTERN.search(line):
                    violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")

    assert not violations, (
        "Found a vatTreatment-shaped field, column, or assignment — ADR-057/S-D10 "
        "forbids this everywhere (model, schema, API, UI). An offer, a contract and "
        "an invoice carry ONE price: gross, CHF incl. MwSt. VAT is a single line on "
        "the printed document only, never a per-deal attribute:\n" + "\n".join(violations)
    )
