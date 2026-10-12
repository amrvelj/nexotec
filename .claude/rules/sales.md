---
paths:
  - "app/sales/**"
  - "scripts/migrate_transaction_rows.py"
  - "frontend/apps/dms/src/**/*ffer*"
  - "frontend/apps/dms/src/**/*ontract*"
  - "frontend/apps/dms/src/**/*ales*"
  - "frontend/apps/dms/src/components/PriceBuildUp.tsx"
  - "frontend/packages/ui-kit/src/components/Sales*"
  - "frontend/packages/ui-kit/src/components/DocumentPreview.tsx"
  - "tests/architecture/test_margin_never_in_rendered_document.py"
  - "tests/architecture/test_no_vat_treatment_field.py"
  - "frontend/apps/dms/src/components/deal-workspace/**"
  - "frontend/apps/dms/src/hooks/useDebouncedNumberField.ts"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises PRD-Sales v2 (S-D10),
ADR-041, ADR-046, ADR-047, ADR-049, ADR-050 (addendum 2026-10-11), ADR-051, ADR-052,
ADR-057, ADR-063, ADR-065, ADR-083 and KAN-66/68/71. Verified against main@7a90ae4 on
2026-10-12 (every present-tense claim checked against the code). "Open" lines cite a ticket;
/drift-audit re-checks them weekly. -->

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
  naming no stock item, on a stock item Stock holds as purchased, last changed over an hour ago,
  with no replica row in its own dealership, and on a replica row whose item Stock does not hold
  as purchased for that dealership — both match on tenant, as `is_invoiceable` does (KAN-145).
  Every cross-context id on `sales_offer` and `sales_contract` (`created_by`/`updated_by`
  included) is reconciled too, on id alone, not on the row's dealership (KAN-259); a contract's
  `reservation_id` only while it is confirmed, since a released hold is cleared on the stock item.
- **ADR-050** — `sales_contract` supersedes the legacy `transaction` table. Legacy rows move via
  `scripts/migrate_transaction_rows.py`: dry-run by default, idempotent, written directly
  through the ORM and **publishing no outbox events** (a years-old sale must not look like
  today's business to live consumers). Its dry run writes nothing but does not flush, so a
  same-pass VIN reuse or `vehicle_already_in_stock` conflict shows only in the `--commit` report.
  A migrated sale stays `CONFIRMED` and `request_invoice` refuses any contract with
  `legacy_transaction_id` set (409, reason `legacy_sale`) — the old system invoiced it (Anto,
  2026-10-10, option 2 of KAN-147). `cancel_contract` refuses it too (same reason; `isLegacy`
  on the read model), so its offer never frees for a second contract (ADR-050 addendum,
  2026-10-11; KAN-147, KAN-277 folded in).
  A legacy trade-in whose customer is not VAT-registered and not `vatStatusConfirmed` is held
  (`supplier_vat_status_unconfirmed`, nothing written), and so is every later row for the same
  car (`held_behind_unconfirmed_trade_in`) — Anto, 2026-10-11, KAN-295.
- **No empty contracts** (Anto, 2026-10-11, KAN-276): `POST /sales/contracts` needs exactly one
  of `offerId`, `customerId` or `stockItemId` (`ContractCreate` refuses none or two, 422).
- **No contract is signed without a buyer** (Anto, 2026-10-12, KAN-307): `confirm_contract`
  refuses a contract with no customer (409 `missing_customer`), before the vehicle and price
  gates. The gap KAN-276 left open (a contract from an offer without a customer) is closed.
  A contract priced without inputs and without a buyer can now be neither given one nor
  confirmed (KAN-315).
- **A contract can start from a car** (Anto, 2026-10-12, KAN-307: "the same as offer
  generation"): `stockItemId` (with the optional display `vehicleLabel`) creates a pending
  contract with that car through the contract's own Path A edit, in the creating transaction.
  The buyer is then picked inside the pending contract, as on the offer: `ContractUpdate` has
  `customerId` (ADR-083 amended — before, a contract kept the customer it was created for),
  applied by the one `offer.apply_customer_change` both hosts use (do-not-contact refused at
  attach). The screens share `DealCustomerContainer`. The stock detail's "Vertrag erstellen"
  uses it, and "Offerte erstellen" creates an offer and sets the same car (POST, then PATCH).
- **One live contract per offer** (KAN-147): `create_contract` refuses an offer that already
  has a non-cancelled contract (409 `offer_already_contracted`, with its `contractNumber`),
  under the offer's row lock; a cancelled contract frees the offer. That refusal is what keeps
  a legacy sale's synthesised offer from taking a second contract.
- **Sales writers take row locks offer → contract → deal** (KAN-147): `upsert_deal_projection`
  flushes its caller's offer/contract changes before it writes the deal, and the customer
  merge's repointing flushes offers, then contracts, then deals — so neither can deadlock a
  contract creation holding the offer. An offer's or contract's row comes before its line
  items: `update_offer`/`update_contract` write the offer or contract before re-freezing its
  factory-option lines, and `replace_line_items` locks it (`priced_deal.lock_editable`) before
  touching a line (KAN-287).

## Price and VAT (ADR-057)

- An offer, a contract and an invoice carry **one price: gross, CHF incl. MwSt**, after all
  discounts. No net line and no VAT breakdown on screen or on the customer document. **There is
  no `vatTreatment`** anywhere — do not reintroduce one.
- VAT is **one line on the printed document only**, computed at the dealership's
  `Dealership.vat_rate` (Notion calls it `dealer_settings.vat_rate` — no such table exists) in
  `app/sales/services/document.py::_price_build_up_lines` (label key `priceBuildUp.includedVat`);
  a dealership with no `vat_rate` gets no VAT line, never a guessed rate.
- **The VAT base when a trade-in is involved is undecided (KAN-68).** Today the line is
  computed on `payable` (after the trade-in), else the gross price (`document.py`). Leave that
  as it is; any change to the base needs Anto's ruling.
- *Why:* Margenbesteuerung for used cars was abolished on 1 January 2010 and replaced by the
  **fiktiver Vorsteuerabzug** (Art. 28a MWSTG). Margin taxation survives only as Art. 24a MWSTG
  for Sammlerstücke (first registration more than 30 years before purchase) — out of scope for
  v1. ADR-033 and ADR-053 are superseded in part by ADR-057; only the fiktiver Vorsteuerabzug
  survives.
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
  Stock creates that item already reserved for the contract and releases it when it consumes
  `sales.contract.cancelled`; Sales makes no reservation call for it and its
  `reservation_id` stays empty (KAN-158). Contracts confirmed before KAN-158 have an
  unreserved item until `scripts/backfill_manual_configuration_reservations.py` (KAN-166, dry
  run by default) is run with `--commit` and reserves it — see `.claude/rules/inventory.md`.
  PRD-Sales S-D10 was corrected to match on 2026-10-04.
- A contract needs a vehicle and a price before it can be confirmed — and before its document
  can be generated (`services/contract_completeness.py`, one rule for both; KAN-67).
- **A pending contract is editable, a confirmed one frozen** (ADR-083, Anto 2026-10-10,
  KAN-67): `PATCH /sales/contracts/{id}` (`ContractUpdate`: the vehicle-and-price subset of
  `OfferUpdate`, plus the buyer since KAN-307), and `POST .../trade-in`, `POST .../trade-in/valuation` and
  `PUT .../line-items` with the offer's own request bodies. All run the offer's own code —
  snapshot, build-up, line items and trade-in take a `PricedDeal` (`services/priced_deal.py`),
  and `services/deal_editing.py` is the one vehicle-and-price edit for both. `sales_contract`
  holds the same pricing inputs as `sales_offer`; a contract from an offer copies them and its
  line items (`contract_id`). Each of these writes refuses a contract that is not pending
  (`contract_not_pending`) and one priced before contracts kept their inputs
  (`contract_priced_without_inputs`; it can still be confirmed or cancelled). Every contract
  route that changes the row loads it `for_update` (`get_contract_or_404`), so an edit and a
  confirmation of the same contract run one after the other — If-Match alone compares the
  version as read and would let an edit land after signing. A trade-in save commits part-way
  (vehicle-mdm and the vehicle parties are their own transactions, ADR-047), which releases
  that lock, so `set_trade_in` locks the deal again after its last foreign write and re-checks
  that it is editable and still at the version the caller sent (KAN-309; the offer's trade-in
  and line-item saves do the same). **Offers lock like contracts** (KAN-311): every offer route
  that changes the row (PATCH, line items, trade-in, trade-in valuation, finalize, cancel) loads
  it `for_update` (`get_offer_or_404`), so two saves of one offer run one after the other and
  the second is a stale-version 409, never a lost update. A failed
  confirmation compensates (releases its reservation, reverts "used") under the contract's row
  lock on a session of its own, and undoes nothing once the contract is signed — a concurrent
  confirmation of the same contract may have committed with the same reservation — or when it
  cannot take that lock (logged, left to the nightly run). Before compensating, the
  confirmation ends its own transactions (request and reservation), so the compensation never
  waits on a lock its own request holds (KAN-192).
  An edit publishes no event. Never give the contract a second copy of a pricing rule.
- **CHF 0.00 is a price** (Anto, 2026-10-10, KAN-67): a car whose base price was never entered
  builds to 0.00 and may be confirmed and printed; only an empty price is `missing_price`
  (`contract_completeness.has_price`). The pricing container still shows "in progress" for it.
- **A trade-in valuation past its validity refuses confirmation** (`trade_in_valuation_expired`,
  KAN-101); confirmation consumes the valuation first, then reserves the car — see
  `.claude/rules/valuation.md`. Contracts before KAN-101 were confirmed without marking their
  valuation used; nightly reconciliation reports every signed contract whose trade-in valuation
  of its own dealership is not used, and «Als verwendet markieren» repairs it, expired or not
  (KAN-115). One naming another dealership's valuation is reported by nothing yet (KAN-259).
- **A car that has left stock cannot be signed again** (KAN-147): confirming a stock-car
  contract whose item has `left_stock_at` set is refused by Stock's `reserve_for_contract`
  (409 `vehicle_left_stock`), and the compensation reverts the trade-in valuation's "used"
  stamp as for any refused reservation. The contract page keeps «Vertrag bestätigen» disabled
  while a price edit is unsaved or any deal save is in flight (`useDebouncedNumberField`,
  `trackSave`; KAN-288).
- **ADR-065** — a credit block stops contract confirmation, not the offer. An address-less
  customer is likewise gated at confirmation (D-20). Do-not-contact refuses the offer, a
  contract created straight from the customer, and every confirmation.

## Documents

- **ADR-063** — generating an offer is two steps: build, then review the rendered document in
  the **customer's correspondence language**, with the seller-only margin panel beside it,
  never on it (architecture test: margin never in a rendered document).
  `build_offer_content` / `build_contract_content` take a required `language`; a record with no
  customer language renders in DE (`_DEFAULT_LANGUAGE`) — a German fallback awaiting a ruling
  (KAN-104).
- **ADR-051** — one shared document template layer (platform); see `.claude/rules/platform.md`.
- **ADR-041** — the offer freezes a `vehicle_snapshot` (`services/snapshot.py`); a pending
  contract freezes its own through the same function (KAN-67).
- **FR-C-12 (KAN-10)** — Path B is the configurator, `build` only: `configurationId` on the
  offer — or on a pending contract (`ContractUpdate`, KAN-67; the same `deal_editing` code) —
  prefills `manualBasePrice`, freezes the spec block and turns the configuration's price lines
  into factory-option lines (re-frozen on a new configuration version while the offer is a
  draft or the contract pending). It travels from the offer to the contract and, as
  `manualConfiguration.configurationId`, onto the pipeline item. A trade-in captured through the
  valuation path carries `tradeInConfigurationId` and has no vehicle-mdm record. The offer
  document itemises the factory-option lines; the contract document does not.

## Visibility and numbers

- **ADR-049** — full commercial visibility inside the dealership (margin, discounts,
  Wagenbuch); **ADR-029** unchanged at the group boundary.
- Offer and contract numbers are **working business keys, not legal document numbers** — the
  gapless legal number is the invoice's (ADR-032, see `.claude/rules/finance.md`).
  `app/sales/services/numbering.py` allocates one counter per (dealership, series — `offer`,
  `contract`) inside the caller's transaction, so a rollback re-issues the same number.
