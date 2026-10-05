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
- **ADR-052** — `is_invoiceable` is Stock's fact; Sales keeps a local replica and never queries
  Stock synchronously. Stock publishes `inventory.stock_item.purchased` once, so the replica is
  kept **per stock item** (`sales_stock_item_purchase`, written by
  `app/sales/services/stock_item_purchase.py`) whether or not a contract exists yet, with
  rule 2's label (`stock_item_label`, the stock number from the event, and
  `stock_item_denorm_refreshed_at`; KAN-150).
  `SalesContract.is_invoiceable` is **derived on read** from it — there is no stored column, so a
  contract written after the purchase is invoiceable at once (KAN-100).
  `scripts/migrate_transaction_rows.py` publishes no events, so it writes the replica row for a
  legacy trade-in's purchase itself. `inventory.stock_item.storno` ("sets it back") is not
  emitted yet, so nothing clears it (KAN-146). Nightly reconciliation alarms on a replica row
  naming no stock item and on a stock item Stock holds as purchased for over an hour with no
  replica row; `sales_contract`'s own references are not reconciled (KAN-145).
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

- **Purchase gate — at invoicing, not at confirmation** (Anto, 2026-10-04, KAN-100; ADR-052,
  PRD-Stock K-12/FR-I-12): the dealership cannot invoice a vehicle it has not bought.
  `request_invoice` refuses a contract whose replica says not purchased
  (`details.reason = "vehicle_not_purchased"`). A contract **may be confirmed** — and the car
  reserved — before the purchase; confirming a manually configured vehicle makes it a pipeline
  stock item; Stock names the contract on `inventory.stock_item.added` (`originContractId`,
  `originRole`), and Sales records the item on the contract (`stock_item_id` plus rule 2's
  label; `vehicle_source` stays `manual`), so the same purchase gate applies (KAN-144).
  PRD-Sales S-D10 was corrected to match on 2026-10-04.
- A contract needs a vehicle and a price before it can be confirmed.
- **A trade-in valuation past its validity refuses confirmation** (`trade_in_valuation_expired`,
  KAN-101); confirmation consumes the valuation first, then reserves the car — see
  `.claude/rules/valuation.md`. Contracts before KAN-101 were confirmed without marking their
  valuation used.
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
