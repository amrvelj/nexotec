---
paths:
  - "app/finance/**"
  - "app/sales/services/numbering.py"
  - "app/**/*numbering*.py"
  - "app/inventory/**/invoicing_gate*.py"
  - "app/sales/consumers.py"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises ADR-032, ADR-037, ADR-052,
ADR-064 and the WP-9 page of the Build Sequence; KAN-74 tracks WP-9. Verified against
main@568f416 on 2026-09-28 (every present-tense claim checked against the code). /drift-audit
re-checks the "not built" line weekly. -->

# Finance, numbering and invoicing

- **WP-9 — numbering, period lock and invoicing — is not built** (KAN-74). `app/finance` is a
  stub with no migration chain yet: its first migration needs a new labelled branch root and
  its folder in `alembic.ini` `version_locations` (see `.claude/rules/migrations.md`). Read the
  WP-9 page and ADR-032 before writing any of it.
- **ADR-032 — gapless, immutable document numbering per legal entity per document type**,
  allocated transactionally, never eventually consistent. Issued documents cannot be modified;
  corrections are credit notes. Closed accounting periods lock against everyone. The archive
  is GeBüV-compliant.
- **ADR-037 — finance is not a ledger:** invoicing and payment matching only.
- **`app/sales/services/numbering.py` is not the WP-9 kernel.** It is offer and contract
  numbering: per dealership, no document-type dimension, no cancellation record, allocated
  inside the caller's transaction (a rollback re-issues the same number). Offer and contract
  numbers are working business keys; the gapless legal number is the invoice's. Do not grow
  `numbering.py` into the kernel.
- **ADR-052:** `is_invoiceable` is Stock's fact, replicated to Sales through
  `inventory.stock_item.purchased` (`app/sales/consumers.py` keeps the local replica); Sales
  never queries Stock synchronously. `inventory/services/invoicing_gate.py::apply_finance_invoice_issued`
  re-asserts it on `finance.invoice.issued` and has no production caller until WP-9.
- On `finance.invoice.issued`, the vehicle's holder transfer **closes** the previous party row
  (ADR-064).
- Open, and Anto's to decide: number ranges per site or continuous across the company (A-26 —
  it decides the gapless design) · the Saldosteuersatz method (A-22 — a tax calculation, not a
  label).
