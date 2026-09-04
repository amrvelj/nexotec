"""Architecture test (WP-8 PR-7, ADR-063/ADR-029): margin, cost_basis and
trade_in_purchase_price never appear in the source that BUILDS a
customer-facing document — the seller-only figures live beside the
document (the margin panel), never on it.

This file was referenced as an existing, green safety net by CLAUDE.md's
WP-8 status notes and by KAN-23's own exit criteria, but never actually
existed (found while building KAN-23) — app/sales/services/document.py's
own module docstring made the same claim. Built now rather than
continuing to cite a test that wasn't there. Mirrors
test_no_vat_treatment_field.py's own pattern: a whole-file source scan
for the FORBIDDEN token used as a real read (an attribute access or a
dict/f-string reference), not a scan of the rendered OUTPUT — the field
must never even be read by the document-building code, the same
structural guarantee the module docstring describes ("enforced
structurally by these functions simply never reading those fields").

tests/test_sales_documents.py's own sentinel-value tests are the
complementary, behavioural half of this guarantee (build a document
against an offer/contract carrying magic sentinel values for these three
fields, assert the sentinels never appear in the dumped content) — this
test is the structural half, catching the field being READ even before
any sentinel-based test happens to notice.
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCUMENT_SOURCE = _REPO_ROOT / "app" / "sales" / "services" / "document.py"

# `.margin`, `.cost_basis`, `.trade_in_purchase_price` as an attribute
# access (offer.margin, contract.cost_basis, ...) — the only way this
# module could ever read one of these fields, since it never imports the
# raw column objects for a query of its own.
_FORBIDDEN_ATTRIBUTES = ("margin", "cost_basis", "trade_in_purchase_price")
_PATTERN = re.compile(r"\.(" + "|".join(_FORBIDDEN_ATTRIBUTES) + r")\b")


def test_document_builder_source_never_reads_margin_cost_basis_or_trade_in_purchase_price():
    text = _DOCUMENT_SOURCE.read_text(encoding="utf-8")
    violations = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if _PATTERN.search(line):
            violations.append(f"{lineno}: {line.strip()}")

    assert not violations, (
        f"{_DOCUMENT_SOURCE.relative_to(_REPO_ROOT)} reads a seller-only figure that must never reach a "
        "customer-facing document (ADR-063/ADR-029) — the margin panel is a separate surface, never on the "
        "document itself:\n" + "\n".join(violations)
    )


def test_the_scanned_file_still_exists():
    """Same anti-vacuous guard every other file-scan test in this
    directory carries — an empty violations list is indistinguishable
    from the file having been renamed out from under this test.
    """

    assert _DOCUMENT_SOURCE.exists(), f"{_DOCUMENT_SOURCE} no longer exists — update this test"


def test_the_pattern_actually_catches_a_deliberate_violation():
    """Anti-vacuous: proves the regex itself can fail, not just that the
    current file happens to pass it.
    """

    for attribute in _FORBIDDEN_ATTRIBUTES:
        line = f"    lines.append(DocumentLine(label='x', amount=offer.{attribute}))"
        assert _PATTERN.search(line), f"pattern did not catch a deliberate offer.{attribute} read"
