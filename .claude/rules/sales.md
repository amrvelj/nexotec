---
paths:
  - "app/sales/**"
  - "scripts/migrate_transaction_rows.py"
  - "frontend/apps/dms/src/**/*ffer*"
  - "frontend/apps/dms/src/**/*ontract*"
  - "frontend/apps/dms/src/**/*ales*"
  - "frontend/apps/dms/src/components/PriceBuildUp.tsx"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises PRD-Sales v2 (S-D10),
ADR-041, ADR-046, ADR-047, ADR-049, ADR-050, ADR-051, ADR-052, ADR-057, ADR-063, ADR-065 and
KAN-66/68/71. Verified against main@568f416 on 2026-09-28 (every present-tense claim checked
against the code). "Open" lines cite a ticket; /drift-audit re-checks them weekly. -->

# Sales: offers, contracts, pricing, documents

## Events and boundaries

- **ADR-046 — two contract events:** `sales.contract.confirmed` at signature (it carries the
  pricing snapshot) and `sales.contract.invoice_requested` at hand-off. Never one name for both
  moments.
- **ADR-047** — a write spanning two contexts is a call with a compensating action, never a
  shared transaction (CLAUDE.md rule 12).
- **ADR-052** — `is_invoiceable` is Stock's fact; Sales keeps a local replica maintained by the
  `inventory.stock_item.purchased` consumer (`app/sales/consumers.py`) and never queries Stock
  synchronously.
- **ADR-050** — `sales_contract` supersedes the legacy `transaction` table. Legacy rows move via
  `scripts/migrate_transaction_rows.py`: dry-run by default, idempotent, written directly
  through the ORM and **publishing no outbox events** (a years-old sale must not look like
  today's business to live consumers); its dry run reports exactly what the real run would
  write.

## Price and VAT (ADR-057)

- An offer, a contract and an invoice carry **one price: gross, CHF incl. MwSt**, after all
  discounts. No net line and no VAT breakdown on screen or on the customer document. **There is
  no `vatTreatment`** anywhere — do not reintroduce one.
- VAT is **one line on the printed document only**, computed at the dealership's
  `Dealership.vat_rate` (Notion calls it `dealer_settings.vat_rate` — no such table exists) in
  `app/sales/services/document.py::_price_build_up_lines` (label key `priceBuildUp.includedVat`).
- **The VAT base when a trade-in is involved is undecided (KAN-68).** Today the line is
  computed on `payable` (after the trade-in), else the gross price (`document.py`). Leave that
  as it is; any change to the base needs Anto's ruling.
- *Why:* Margenbesteuerung for used cars was abolished on 1 January 2010 and replaced by the
  **fiktiver Vorsteuerabzug** (Art. 28a MWSTG). Margin taxation survives only as Art. 24a MWSTG
  for Sammlerstücke (first registration more than 30 years before purchase) — out of scope for
  v1. ADR-033 and ADR-053 are superseded.
- The fiktiver Vorsteuerabzug is a **purchase-side** fact owned by Stock. Sales reads it
  through the stock item's pricing (`landedCost`, `notionalInputTax*`) to compute margin and
  never writes it. Margin nets the notional credit whether or not a landed cost was recorded.

## Contract rules

- **Purchase gate (S-D10):** a contract on a stock vehicle may not be confirmed until that
  stock item's purchase is booked — otherwise the dealership sells something it has not
  acquired. **Not enforced today:** `confirm_contract` has no purchase check. When it is built,
  it reads the local replica (ADR-052), never a synchronous query.
- A contract needs a vehicle and a price before it can be confirmed.
- **ADR-065** — a credit block stops the contract, not the offer. An address-less customer is
  likewise gated at the contract (D-20).

## Documents

- **ADR-063** — generating an offer is two steps: build, then review the rendered document in
  the **customer's correspondence language**, with the seller-only margin panel beside it,
  never on it (architecture test: margin never in a rendered document).
  `build_offer_content` / `build_contract_content` take a required `language`.
- **ADR-051** — one shared document template layer (platform); see `.claude/rules/platform.md`.
- **ADR-041** — the offer freezes a `vehicle_snapshot` (`services/snapshot.py`).

## Visibility and numbers

- **ADR-049** — full commercial visibility inside the dealership (margin, discounts,
  Wagenbuch); **ADR-029** unchanged at the group boundary.
- Offer and contract numbers are **working business keys, not legal document numbers** — the
  gapless legal number is the invoice's (ADR-032, see `.claude/rules/finance.md`).
  `app/sales/services/numbering.py` allocates per dealership inside the caller's transaction,
  so a rollback re-issues the same number.
