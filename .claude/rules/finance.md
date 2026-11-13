---
paths:
  - "app/finance/**"
  - "app/sales/services/numbering.py"
  - "app/**/*numbering*.py"
  - "app/inventory/**/invoicing_gate*.py"
  - "app/sales/consumers.py"
---
<!-- Maintainer note. ADR-032, ADR-037, ADR-052, ADR-064, ADR-109, the ADR-050 addendum and the WP-9
page of the Build Sequence (KAN-74); D-A-17, D-A-27. KAN-615 (2026-11-13) removed restatements of
sales.md. Last verified against main@73baf3f (2026-10-25); /drift-audit re-checks "not built". -->

# Finance, numbering and invoicing

- **WP-9 — numbering, period lock and invoicing — is not built** (KAN-74). `app/finance` is a stub
  with no migration chain yet (its first migration: `migrations.md`). Read the WP-9 page and
  ADR-032 before writing any of it.
- **ADR-032 — gapless, immutable document numbering per legal entity per document type**,
  allocated transactionally, never eventually consistent. Issued documents are never modified;
  corrections are credit notes. Closed accounting periods lock against everyone. The archive is
  GeBüV-compliant. **ADR-037 — finance is not a ledger:** invoicing and payment matching only.
- **Document numbers come from Platform's number ranges** (ADR-109):
  `platform.public.allocate_number(db, dealership_id=, document_type=…, location_id=)` **inside the
  transaction that writes the document** — a row lock on the Betrieb's range, never a commit — so a
  failed write consumes no number; the range freezes at its first number (A-RULE-3). The
  `invoice`, `credit_note` and `dunning` ranges exist and are seeded; nothing draws from them until
  WP-9. ADR-109 rules this shared transaction for number allocation alone.
- **`app/sales/services/numbering.py` is not the WP-9 kernel** — Sales' contract counter and, until
  KAN-540, the old offer counter kept in step (`sales.md`). Do not grow it into the kernel.
- **ADR-052:** invoicing reads Stock's `is_invoiceable` through Sales' replica, never
  synchronously (`sales.md`); `request_invoice` refuses a car not bought and a legacy contract.
  `inventory/services/invoicing_gate.py::apply_finance_invoice_issued` re-asserts it on
  `finance.invoice.issued` and has no production caller until WP-9.
- When WP-9 issues invoices, the holder transfer on `finance.invoice.issued` goes through
  `app.customer.public.allocate_vehicle_party`, which closes the previous party row (ADR-064). Not
  wired today.
- Number ranges are **gapless per legal entity**, one sequence across all sites, `{location_code}`
  a label only (D-A-17, ADR-032); the **Saldosteuersatz** method is **not in v1** (D-A-27). Read the
  PRD's Number ranges and Master data sections before building either.
