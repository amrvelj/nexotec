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
  - "frontend/apps/dms/src/hooks/useDealSaveQueue.ts"
  - "tests/architecture/test_adr_047_no_shared_transaction.py"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises PRD-Sales v2 (S-D10),
ADR-041, ADR-046, ADR-047, ADR-049, ADR-050 (addendum 2026-10-11), ADR-051, ADR-052,
ADR-057, ADR-063, ADR-065, ADR-083 (as amended 2026-10-12), ADR-086, ADR-088, ADR-098 and KAN-66/68/71;
also cites ADR-029, ADR-032, ADR-033, ADR-053, ADR-091. Verified against main@73baf3f on
2026-10-25 (every present-tense claim checked against the code). "Open" lines cite a ticket;
/drift-audit re-checks them weekly. -->

# Sales: offers, contracts, pricing, documents

## Events and boundaries

- **ADR-046 — two contract events:** `sales.contract.confirmed` at signature (it carries the
  pricing snapshot) and `sales.contract.invoice_requested` at hand-off. Never one name for both
  moments. **The hand-off carries the plate the car leaves with** (KAN-523; Anto, 2026-11-01: entered in
  «Rechnung anfordern», which later becomes «Rechnung generieren»): the request's optional body
  (`ContractInvoiceRequest`: `plate` + `canton`, or neither for «ohne Kontrollschild»; `moveFrom`)
  is checked first against the group's plate register through `vehicle.public.check_plate_handover`
  on a session of its own (`services/invoice_handover.py`; 409 `plate_current_on_other_vehicle` names
  the other car through the audited plate-read guard until the user confirms the move — Anto: ask, as
  Vehicle 360 does), the request is audited (`invoice_requested`, whether a plate went with it, never its
  value), and the payload gains `vehicleId`, `vin`, `groupId`, `plate`, `canton`, `moveFrom`,
  `requestedOn` and `requestedBy` (the car's fields null without a plate). The contract page's primary
  action on a confirmed contract is «Rechnung anfordern» (`components/contract-detail/RequestInvoiceDialog.tsx`).
  **Once per contract** (Anto, 2026-11-03, KAN-539 option A): the request stamps
  `sales_contract.invoice_requested_at` (and bumps `version`) in the transaction of its outbox row; a
  second one is refused before any write — a stale screen by its If-Match, one with the current version
  by 409 `invoice_already_requested` (with `invoiceRequestedAt`) — and the page keeps the action visible
  but disabled, naming the day. The migration took each contract's stamp from its earliest
  `sales.contract.invoice_requested` outbox row or `invoice_requested` audit row (the outbox is never
  purged), so a contract requested before it — before KAN-523 too — is refused as well (Anto confirmed
  this backfill, 2026-11-04, option A).
- **ADR-047** — a write spanning two contexts is a call with a compensating action, never a
  shared transaction (CLAUDE.md rule 12). The one bounded exception is
  `scripts/migrate_transaction_rows.py` (ADR-091, Anto 2026-10-12, KAN-106): it writes Sales and
  Inventory in one transaction, imports Inventory's `models`/`services` directly, reads the frozen
  legacy `vehicle` row directly and reads the customer, dealership and `vehicle_mdm` rows it resolves
  against through their models, all four named in its docstring and pinned by
  `test_adr_047_no_shared_transaction.py`; it expires when the script is deleted
  after its production run, and nothing else may cite it. Number allocation is the second ruled
  exception, permanent and Platform's (ADR-109, KAN-207): the offer's number is handed out inside the
  offer's own transaction (below, "Visibility and numbers").
- **ADR-052** — `is_invoiceable` is Stock's fact; Sales keeps a local replica and never queries
  Stock synchronously. Stock publishes `inventory.stock_item.purchased` once per booking, so the replica is
  kept **per stock item** (`sales_stock_item_purchase`, written by
  `services/stock_item_purchase.py`) whether or not a contract exists yet, with
  rule 2's label (`stock_item_label`, the stock number from the event, and
  `stock_item_denorm_refreshed_at`; KAN-150).
  `SalesContract.is_invoiceable` is **derived on read** from it — there is no stored column, so a
  contract written after the purchase is invoiceable at once (KAN-100). An offer and a contract on a stock car carry the same pair (KAN-157,
  Anto 2026-11-03, option A): copied once, when the car is put on the deal, from the `stockNumber`
  in `inventory.public.get_stock_item_pricing` that the snapshot freeze reads anyway; cleared
  with `stock_item_id` (`deal_editing`); a contract from an offer and a copied offer take the
  offer's; never refreshed (it never changes). `scripts/backfill_sales_stock_item_labels.py` fills the rows written before. **It is on screen** (KAN-570; Anto,
  2026-11-05, option A): `OfferRead`/`ContractRead.stockItemLabel` (additive); «Lagernummer S-…»
  beside the car (`StockNumberLink`) in the vehicle container of the offer workspace and the pending
  contract, and in the header of the sent offer and of a contract past editing, opening the stock
  detail as an overlay (ADR-059); and on the deal row, `sales_deal.stock_item_label` (Sales-chain
  migration `e7a3c5d1b9f4`, indexed), written by `upsert_deal_projection` from the live entity,
  compared by the deal-row StateChecks, filled for older rows by
  `scripts/backfill_sales_deal_projection.py` (`--commit` **once per
  environment after deploy**) — the Sales grid's optional «Lagernummer» column, hidden by
  default, sortable (`sort=stockItemLabel`).
  `scripts/migrate_transaction_rows.py` publishes no events, so it writes the replica row for a
  legacy trade-in's purchase itself. `inventory.stock_item.storno` ("sets it back", KAN-146) is
  consumed as `sales.stock_item_storno`: it deletes the replica row (locked first, as the purchase
  consumer locks it), so every contract
  on the car derives «nicht fakturierbar» until the next purchase — unless Stock holds the car as
  bought again by then, which the consumer re-reads through `inventory.public.is_stock_item_purchased`
  (delivery is unordered: a storno may arrive after the next purchase). That consumer's read is
  asynchronous; `request_invoice` still reads only the local replica (ADR-052 addendum, Anto
  2026-11-06, KAN-146 option A: "never a synchronous query" governs the invoicing path). Unhandled: a purchase whose
  first delivery failed arriving after its storno recreates the row — the KAN-145 check below
  reports it once the storno is an hour old. Nightly reconciliation alarms on a replica row
  naming no stock item, on a stock item Stock holds as purchased, last changed over an hour ago,
  with no replica row in its own dealership, and on a replica row whose item Stock does not hold
  as purchased for that dealership — both match on tenant, as `is_invoiceable` does (KAN-145) —
  unless Stock stornoed that item for the dealership less than an hour ago (the storno event's
  `occurred_at`; KAN-146) — and on a contract, not cancelled, whose invoice was requested for a
  stock item Stock does not hold as purchased in its dealership (the storno / «Rechnung anfordern»
  race; KAN-146).
  Every cross-context id on every Sales table is reconciled (KAN-145, KAN-243): an id naming
  nothing is an orphan (ReferenceCheck); a stock item, configuration or valuation of another
  dealership, and a customer outside the dealership's group, is a mismatch (StateCheck, KAN-259);
  a contract's `reservation_id` only while it is confirmed, since a released hold is cleared on
  the stock item. A Sales row naming a merged customer last written over an
  hour ago is a `customer.merged` `sales.customer_merged` missed (KAN-581; an attach waits on it, KAN-657). `created_by`/`updated_by` name a `user.id` or are null in every reconciled
  context (Anto, 2026-10-12, KAN-243: `platform.public.actor_reference_checks`, enforced by
  `test_actor_columns_reconciled.py`).
- **ADR-050** — `sales_contract` supersedes the legacy `transaction` table. Legacy rows move via
  `scripts/migrate_transaction_rows.py`: dry-run by default, idempotent, written directly
  through the ORM and **publishing no outbox events** (a years-old sale must not look like
  today's business to live consumers). Its dry run runs the real path and rolls it back (any
  commit on the way is refused), so it reports exactly what `--commit` writes, same-pass VIN
  reuse and `vehicle_already_in_stock` included (KAN-263); a sale or trade-in whose dealership
  does not exist is `dealership_unresolved`, never written (KAN-265).
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
  A pending contract priced without inputs and without a buyer may be given one by a PATCH
  carrying `customerId` alone (Anto, 2026-10-12, KAN-315) — price untouched — and then
  confirmed; a PATCH carrying `ownerUserId` alone is accepted too (KAN-405); any other field
  on it is still `contract_priced_without_inputs`.
- **A contract can start from a car** (Anto, 2026-10-12, KAN-307: "the same as offer
  generation"): `stockItemId` (with the optional display `vehicleLabel`) creates a pending
  contract with that car through the contract's own Path A edit, in the creating transaction.
  The buyer is then picked inside the pending contract, as on the offer: `ContractUpdate` has
  `customerId` (ADR-083 amended),
  applied by the one `offer.apply_customer_change` both hosts use (do-not-contact or a merged
  customer refused at attach). The screens share `DealCustomerContainer`. The stock detail's "Vertrag erstellen"
  uses it, and "Offerte erstellen" creates an offer and sets the same car (POST, then PATCH).
- **One live contract per offer** (KAN-147): `create_contract` refuses an offer that already
  has a non-cancelled contract (409 `offer_already_contracted`, with its `contractNumber`),
  under the offer's row lock; a cancelled contract frees the offer. That refusal is what keeps
  a legacy sale's synthesised offer from taking a second contract.
- **An offer with a live contract is read-only** (Anto, 2026-10-21, KAN-408 option A): its
  PATCH, line items, trade-in and trade-in valuation (`priced_deal.require_editable`), finalize
  and cancel are 409 `offer_has_contract` with the `contractNumber` — the contract is edited
  itself while pending (ADR-083), so the two never disagree. A cancelled contract frees the
  offer, editable again while it is a draft. `OfferRead` names that contract
  (`liveContractId`, `liveContractNumber`; computed in `api/offers.py::_offer_reads`, one query
  per page — KAN-492), so a sent offer's seller picker and «Übernehmen» are disabled before the
  click; «Übernehmen» names the contract in the menu (`dealOwner.takeOverDisabled.liveContract`),
  the picker carries no reason line (Anto, 2026-10-29: «Disabled is disabled»). Generating an
  offer document is refused too (Anto, 2026-10-29, KAN-495 option a:
  `document.generate_offer_document` asks `require_no_live_contract`), and the sent offer's
  «Dokument erzeugen» is disabled before the click and says why — an action, not a field
  (`salesDocuments.liveContract`). The draft workspace reads it as well (KAN-426): such an offer
  renders read-only (disabled fieldsets over every write control, «Offerte generieren» disabled with
  the reason) under a banner naming the contract with «Vertrag öffnen», and every container
  words a save refused `offer_has_contract` in that banner's sentence. What only shows stays live
  (KAN-501): the customer, vehicle and pricing containers take `readOnly` themselves
  (`components/deal-workspace/LockedWhile.tsx` around their writes), so the selected customer's
  overlay, the stock number (KAN-570) and «zum Lagerfahrzeug» still open.
- **The deal row follows its deal's live entity** (`deal_projection.py`; KAN-408, KAN-409):
  the offer's live contract while it has one, else the offer — whichever side the caller wrote.
  A cancelled contract hands the row back to its offer (its number, status, buyer, car, seller;
  no margin), so the grid, the seller filter and the unassigned queue find a freed offer; the
  cancelled contract stays on the customer's contracts and the offer's history. A contract
  without an offer keeps its own row, cancelled or not. Generating a document re-projects the
  row, so its document count moves with it. Nightly reconciliation reports a row whose number,
  status, buyer, locality, car, stock number (KAN-570), seller or document count differs from its live entity, or whose
  shape is wrong (two StateChecks, rule 10); `scripts/backfill_sales_deal_projection.py` (dry run
  unless `--commit`) re-projects the rows written before KAN-408/KAN-409.
- **Sales writers take row locks offer → contract → deal** (KAN-147): `upsert_deal_projection`
  flushes its caller's offer/contract changes before it writes the deal, and the customer
  merge's repointing flushes offers, then contracts, then deals — so neither can deadlock a
  contract creation holding the offer. An offer's or contract's row comes before its line
  items: `update_offer`/`update_contract` write the offer or contract before re-freezing its
  factory-option lines, and `replace_line_items` locks it (`priced_deal.lock_editable`) before
  touching a line (KAN-287).

## The responsible seller (KAN-200, Anto 2026-10-19)

- `sales_offer`, `sales_contract` and `sales_deal` carry `owner_user_id` + `owner_label` +
  `owner_label_refreshed_at` (rule 2): who the open deal belongs to — the creator by default
  when they act in the Betrieb (homed or an active member, active or invited — `platform.public.get_active_user_acting_in`, the customer's advisor's rule too, KAN-410),
  a colleague after a hand-over, nobody once a leaver's deals went to the unassigned queue.
  `created_by` stays who typed it (A-RULE-5). The workspace PATCH takes `ownerUserId` (422 for
  someone who does not act there or is suspended/deactivated; null clears) — and, alone in the
  PATCH, also on an open offer without a live contract and on a pending contract priced without
  inputs, its price untouched (Anto, 2026-10-28, KAN-405 option A); the screens send it from
  `DealOwnerSelect` (offer workspace, sent offer, pending contract), and «Übernehmen» — one
  action, `components/dealRowMenu.tsx`, in the Sales list's row menu and both detail overflows
  (ADR-061) — takes an unassigned open deal for the signed-in user; a contract takes
  its offer's seller; `GET /sales/deals` filters `ownerUserId` and `unassigned=true` (the Sales
  list's "Nicht zugewiesen" chip — open offers and pending contracts; drafts stay hidden — and its
  "Verantwortlich" column). The deal row's seller is its live entity's: the live contract's
  while there is one, the offer's before and again after that contract is cancelled (KAN-409).
  A contract made from an offer takes the offer's seller only while that seller
  is active or invited (`inherited_owner`), else its creator's. `services/ownership.py`
  is Sales' ownership provider for the deactivation step (`platform-users.md`, D-A-35):
  it counts the user's open offers and pending contracts in every Betrieb and refuses a colleague
  who cannot act in one of them. Nightly reconciliation reports an open deal whose owner is a
  deactivated user and an owner id naming no user. Both labels follow a rename overnight, by the
  advisor job's rules (no `version` bump, unknown ids left alone, batches `FOR NO KEY UPDATE
  SKIP LOCKED`, offer → contract → deal): `sales.owner_labels` (KAN-404; a deactivated
  seller's kept) and `sales.customer_labels` (KAN-590, Anto 1A; see its docstring):
  the buyer's name and language on open work only — signed work keeps its name, an offer
  with a live contract of its own buyer mirrors it (rule 2). Gauges `sales.owner`,
  `sales.customer`. `customer_locality` stays unfilled (ruled).

## Price and VAT (ADR-057)

- An offer, a contract and an invoice carry **one price: gross, CHF incl. MwSt**, after all
  discounts. No net line and no VAT breakdown on screen or on the customer document. **There is
  no `vatTreatment`** anywhere — do not reintroduce one.
- VAT is **one line on the printed document only**, computed at the dealership's
  `Dealership.vat_rate` (Notion calls it `dealer_settings.vat_rate` — no such table exists) in
  `services/document.py::_price_build_up_lines` (label key `priceBuildUp.includedVat`),
  directly under «Verkaufspreis», before the trade-in and «Zu bezahlen» (Anto, 2026-10-28,
  KAN-434 option A — printed last it read as the VAT inside the payable);
  a dealership with no `vat_rate` gets no VAT line, never a guessed rate.
- **The VAT line is always computed on the full gross price, never on `payable`** (ADR-098,
  Anto's ruling 2026-10-24, KAN-68). The sale and the trade-in are two supplies (Art. 24 Abs. 3
  MWSTG): the trade-in pays part of the price, it does not reduce it. Gross CHF 36,000 with a
  CHF 10,000 trade-in at 8.1 % prints CHF 2,697.50, not CHF 1,948.20. Open: no tax advisor
  has confirmed it yet (KAN-436); if one reads the trade-in as a price reduction, ADR-098 is
  reopened, never overridden in code. Documents generated earlier keep their frozen figure.
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
  (`contract_priced_without_inputs`; it can still be confirmed or cancelled, given a
  buyer it lacks — KAN-315 — and given a responsible seller — KAN-405). Every contract
  route that changes the row loads it `for_update` (`get_contract_or_404`), so an edit and a
  confirmation of the same contract run one after the other — If-Match alone compares the
  version as read and would let an edit land after signing. A trade-in save is one transaction
  since KAN-189 (it creates no vehicle-mdm record and allocates no party; it only reads the
  register), so it holds that lock to its commit (`tests/test_sales_trade_in_lock.py`);
  `set_trade_in` still re-locks and re-checks editability and the caller's version before writing
  (KAN-309, from when it committed part-way; the line-item saves do the same). **Offers lock like contracts** (KAN-311): every offer route
  that changes the row (PATCH, line items, trade-in, trade-in valuation, finalize, cancel) loads
  it `for_update` (`get_offer_or_404`), so two saves of one offer run one after the other and
  the second is a stale-version 409, never a lost update. A failed
  confirmation compensates (releases its reservation, reverts "used") under the contract's row
  lock on a session of its own, and undoes nothing once the contract is signed — a concurrent
  confirmation of the same contract may have committed with the same reservation — or when it
  cannot take that lock (logged, left to the nightly run). Before compensating, the
  confirmation ends its own transactions (request and reservation), so the compensation never
  waits on a lock its own request holds (KAN-192).
  An edit publishes no event — except that an offer or contract starting to hold a configuration id
  (Path B, the trade-in valuation, a copied offer, a contract from its offer) publishes
  `sales.offer.configuration_attached` / `sales.contract.configuration_attached` in its own
  transaction, so Vehicle keeps a configuration a host took (KAN-270, `vehicle-configurator.md`);
  `sales.public.referenced_configuration_ids` is Vehicle's read-only safety net. Never give the contract a
  second copy of a pricing rule.
- **One set of bounds for the shared pricing fields** (Anto, 2026-10-28, KAN-482: no negatives):
  `OfferUpdate` takes `ContractUpdate`'s bounds for `manualBasePrice`, `discountType` and
  `discountValue` (`ge=0`, 12 digits, 2 decimals; `percent` | `amount`), and the offer's leasing
  figures are never negative. `test_sales_offer_pricing_bounds.py` compares the two schemas field by
  field. The five inputs (base price, discount, the three leasing figures) never send a negative
  figure (KAN-493): `useDebouncedNumberField`'s `nonNegative` holds one like a partial entry — shown
  as typed, marked `common.notNegative`, unsaved, never committed. The inputs still take «-» (one
  refusing it empties itself when its text is replaced by «-», and that clear would be saved —
  KAN-480's hazard).
- **A discount never exceeds what it is taken off** (KAN-190): `pricing.resolve_discount` caps
  the resolved amount at its base — the line, or the deal's total before discount — for an
  amount and for a percentage over 100, so no line and no total goes negative (a kept discount
  re-applied to a cheaper re-frozen line, KAN-10). The typed `discountValue` is stored as typed.
- **CHF 0.00 is a price** (Anto, 2026-10-10, KAN-67): a car whose base price was never entered
  builds to 0.00 and may be confirmed and printed; only an empty price is `missing_price`
  (`contract_completeness.has_price`). The pricing container still shows "in progress" for it.
- **A trade-in valuation past its validity refuses confirmation** (`trade_in_valuation_expired`,
  KAN-101); confirmation consumes the valuation first, then reserves the car — see
  `.claude/rules/valuation.md`. Contracts before KAN-101 were confirmed without marking their
  valuation used; nightly reconciliation reports every signed contract whose trade-in valuation
  of its own dealership is not used, and «Als verwendet markieren» repairs it, expired or not
  (KAN-115). One naming another dealership's valuation is a reconciliation mismatch (KAN-259).
- **A car that has left stock cannot be signed again** (KAN-147): confirming a stock-car
  contract whose item has `left_stock_at` set is refused by Stock's `reserve_for_contract`
  (409 `vehicle_left_stock`), and the compensation reverts the trade-in valuation's "used"
  stamp as for any refused reservation. The contract page keeps «Vertrag bestätigen» disabled
  while a price edit is unsaved or any deal save is in flight (`useDebouncedNumberField`;
  KAN-288); the offer workspace's «Offerte generieren» likewise waits for an unsaved price or
  leasing value and for queued saves (KAN-327, KAN-344) and says why it is disabled (KAN-368),
  so the reviewed document is what was entered (ADR-063). Both deal workspaces send their saves through `hooks/useDealSaveQueue.ts`: one
  after the other, each with the version the previous one returned, a refused save said and
  never retried (KAN-314) — the server's offer/contract row lock refuses the second of two
  saves sent with the same version (KAN-311).
- **ADR-065** — a credit block stops contract confirmation, not the offer. An address-less
  customer is likewise gated at confirmation (D-20). Do-not-contact refuses the offer, a
  contract created straight from the customer, and every confirmation; a merged one every attach
  (409 `customer_merged`, `mergedIntoCustomerId`; KAN-656).

## Documents

- **ADR-063** — generating an offer is two steps: build, then review the rendered document in
  the **customer's correspondence language**, with the seller-only margin panel beside it,
  never on it (architecture test: margin never in a rendered document).
  `build_offer_content` / `build_contract_content` take a required `language`; a record with no
  customer language renders in the Betrieb's own default for customers
  (`platform.public.get_dealership_default_correspondence_language`, Customers FR-13), never a
  silent German (Anto, 2026-11-03, KAN-104 decision 3 A; `document.py::_document_language`).
- **ADR-051** — one shared document template layer (platform); see `platform-documents.md`.
- **The offer's wording is the Betrieb's standard text** (KAN-208): `generate_offer_document`
  resolves `offer` / `intro` (the first paragraph) and `offer` / `footer` (`footer_text`) through
  `platform.public.render_standard_text` in the document's language, with Sales' facts as values
  (`offer_placeholder_values`: number, date, vehicle label, the responsible seller's label). A
  token Sales cannot fill — no car, no seller — fails the generation (422
  `standard_text_placeholder_unresolved`) before anything is written. `generate_contract_document`
  resolves `contract` / `footer` the same way (`contract_footer`, the contract's own facts) — the
  footer a contract printed from the document template before KAN-208 is the Betrieb's default
  contract footer text since then. Sales holds no text column of its own.
- **ADR-041** — the offer freezes a `vehicle_snapshot` (`services/snapshot.py`); a pending
  contract freezes its own through the same function (KAN-67).
- **FR-C-12 (KAN-10)** — Path B is the configurator, `build` only: `configurationId` on the
  offer — or on a pending contract (`ContractUpdate`, KAN-67; the same `deal_editing` code) —
  prefills `manualBasePrice`, freezes the spec block and turns the configuration's price lines
  into factory-option lines (re-frozen on a new configuration version while the offer is a
  draft or the contract pending). It travels from the offer to the contract and, as
  `manualConfiguration.configurationId`, onto the pipeline item. **A copied offer gets a
  configuration of its own** (FR-C-11, KAN-188): `copy_offer` calls
  `vehicle.public.copy_configuration_for_host` on a session of its own before writing the offer and
  `discard_configuration_copy` if that write fails (ADR-047); the trade-in keeps the configuration
  its valuation was judged on (Anto, 2026-10-28, option A). A trade-in captured through the
  valuation path carries `tradeInConfigurationId` and has no vehicle-mdm record. The offer
  document itemises the factory-option lines; the contract document does not.

## Visibility and numbers

- **ADR-049** — full commercial visibility inside the dealership (margin, discounts,
  Wagenbuch); **ADR-029** unchanged at the group boundary.
- Offer and contract numbers are **working business keys, not legal document numbers** — the
  gapless legal number is the invoice's (ADR-032, see `.claude/rules/finance.md`).
  `services/numbering.py` allocates the contract's number from Sales' own counter per
  dealership inside the caller's transaction, so a rollback re-issues the same number. **The offer's
  number comes from the Betrieb's `offer` number range in Platform** (KAN-207, ADR-109:
  `platform.public.allocate_number`, inside the offer's transaction): it continues the sequence Sales'
  own `offer` counter reached (Anto, 2026-11-01, option 1). During the switch that counter is locked
  first, passed as `continue_after` (the range never numbers at or below it) and kept in step, so an
  instance still on the old code during a rolling deploy never repeats a number; lock order Sales'
  counter → Platform's range; retired by KAN-540. The legacy transaction import keeps Sales' counter
  (`allocate_legacy_offer_number`) and never consumes or freezes the range.
  `scripts/continue_offer_number_ranges.py` (`--commit` **once per environment
  after deploy**) shows each Betrieb's current value on the Number ranges screen before its next
  offer.
