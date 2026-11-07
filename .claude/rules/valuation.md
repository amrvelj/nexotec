---
paths:
  - "app/valuation/**"
  - "app/sales/services/trade_in.py"
  - "app/sales/services/contract.py"
  - "app/inventory/services/pipeline.py"
  - "app/sales/services/signed_trade_ins.py"
  - "app/sales/reconciliation.py"
  - "app/inventory/reconciliation.py"
  - "frontend/apps/dms/src/**/*aluation*"
  - "app/inventory/services/valuation.py"
  - "app/inventory/schemas/valuation.py"
  - "frontend/packages/ui-kit/src/components/Valuation*Badge.tsx"
  - "alembic/versions/valuation/**"
  - "frontend/apps/dms/src/components/deal-workspace/**"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises PRD-Vehicles (FR-V-09,
FR-V-17), ADR-029, ADR-048 as amended, ADR-066, ADR-070, ADR-074; also cites ADR-047. Verified
against main@73baf3f on 2026-10-25 (every present-tense claim checked against the code). Fix this
file in the same PR as any change to what it states. -->

# Valuations

- **`valuation` is its own bounded context** — "a dated commercial opinion, not a vehicle
  fact"; the PRD gives it its own audit and retention rules, not implemented yet. Not under
  `vehicle` (vehicle identity is global, valuations are tenant-private) and not under `sales`
  (Sales owns the trade-in workflow, not the valuation record).
- **Single writer.** Sales holds `trade_in_valuation_id` (offer and contract) and Stock
  `valuation_ref_*`, both read-only references to the same record; there is never a second
  writer.
- **Tenant-private even within a group** (ADR-029): a sister dealership never sees another's
  valuations.
- **A vehicle carries a LIST of valuations** (ADR-048 as amended, ADR-066). The newest is
  current; older ones stay readable and are never edited or deleted (only the `used_at` stamp
  changes). «Neu bewerten» creates a new row whose `supersedesValuationId` names the old one;
  no read marks the old row superseded yet. A manual
  figure is **marked manual everywhere it renders**. «Neu bewerten» is the detail's primary; its
  alternative, also in the grid's row menu (`valuationRowMenu.tsx`, ADR-061), is «Offerte aus
  dieser Bewertung erstellen» (KAN-569; Anto 2026-11-05, 1A/2A): a draft offer, the valuation's
  customer when it has one (none otherwise), the valuation attached as trade-in, then the
  workspace — a draft valuation allowed, an expired or used one disabled with the reason, hidden
  without the `sales_offers` write; a refused later step keeps the screen, names the offer made,
  and a retry continues that offer. A valuation made **without a configuration** brings its car
  to a deal that names no trade-in car yet — its stored VIN, linked record and «Make Model Trim»,
  or the VIN as the label when it has no make or model (the confirmation publishes the label and
  Stock names the pipeline item by it) (`sales/services/trade_in.py::_attach`, KAN-569) — so
  `has_trade_in` holds and the confirmation takes the car into the pipeline; a deal that already
  names its car keeps it. **A trade-in is a car** (KAN-574; Anto, 2026-11-07, option A): older
  valuations without a VIN (rows from before the rule below) are refused as trade-ins — 422
  `trade_in_valuation_names_no_car` at attach, nothing written, whatever the deal already names and
  configured or not, and never offered by `set_trade_in`'s FR-S-08 auto-attach — and «Offerte aus dieser Bewertung erstellen» is disabled for them with the
  reason (`createOfferNoVin`). **Open with Anto on KAN-574:** a pre-1981 chassis number is refused
  the same way (a trade-in takes a VIN until KAN-535); a deal that held such a valuation before the
  rule keeps it (copy, contract and confirmation do not re-check).
- **A valuation names its car** (KAN-574; Anto, 2026-11-07, option A): `POST /v1/valuations` needs
  a VIN (or a pre-1981 chassis number, KAN-353), a make and a model — each defaulted from the
  configuration when `configurationId` is sent (a field sent wins, ADR-070), typed otherwise — else
  422 `valuation_names_no_car` with the missing `fields`, judged after the chassis-number rule. A
  VIN typed beside a configuration without one is stored (KAN-449 option A) — **open with Anto on
  KAN-574** (the ruling asked the configuration to carry it); `ValuationCreateDialog` marks the three required and holds its submit
  until they are filled. Rows written before keep what they had.
- **A valuation is a standalone application**: creatable with no customer, no offer and no
  vehicle in the register. It carries a **validity period**. Its routes are licensed with the
  `sales` module (`require_entitlement(ModuleCode.SALES)` on the router, KAN-198): 404 without it.
- **Status `draft → valid → expired`** (plus `used` once a contract consumes it, or once the
  manual `POST /v1/valuations/{id}/mark-used` correction sets it) is **derived on read** from
  `is_draft`, `valid_until` and `used_at` — never stored, never repaired by a nightly job.
- **A contract consumes its trade-in valuation at confirmation** (KAN-101):
  `sales/services/contract.py::confirm_contract` calls
  `valuation.public.consume_valuation_for_contract` on its own session (ADR-047), **before**
  reserving the car, so a refusal never leaves a reservation to undo. **One
  valuation may back several contracts** (Anto, 2026-09-29) — the same VIN may be in pipeline
  twice, never twice in stock. A valuation past `valid_until` refuses the confirmation
  (`trade_in_valuation_expired`); a draft does not. If the reservation or the contract's own
  commit fails, `revert_valuation_use` undoes "used" only when that confirmation set it and no
  other signed contract carries it; each compensating action runs even if the other fails.
  Neither runs once the contract itself is signed — a concurrent confirmation committed — nor
  when the compensation cannot lock the contract row (KAN-192; logged, left to the nightly run).
  **Cancelling a signed contract leaves it used** (ADR-066).
- **Only a signed deal uses a valuation** (Anto, 2026-10-07, KAN-115). «Als verwendet
  markieren» (`POST /v1/valuations/{id}/mark-used`, `services/valuation.py::mark_used_by_hand`)
  repairs a missed stamp: allowed only when a signed contract of the same dealership
  (`signed_at` set, cancelled included) carries the valuation, **whatever its status, expired
  included** — the contracts signed before KAN-101 stamped nothing; refused otherwise
  (`no_signed_contract`). Sales answers through
  `sales.public.valuations_carried_by_signed_contracts`; `ValuationRead.hasSignedContract`
  carries the same answer to the row menu. `mark_used` is the one place that sets `used_at`;
  `revert_use` the one that clears it — the confirmation's compensation, and «Verwendung
  aufheben» below.
- **«Verwendung aufheben»** (Anto, 2026-10-31, KAN-253 option C): `POST
  /v1/valuations/{id}/revert-use` (`services/valuation.py::revert_use_by_hand`; If-Match; a
  mandatory `reason`, ≤ 500 characters) clears a stamp no signed contract of the dealership
  stands behind — a hand stamp from before KAN-115, or a failed confirmation whose contract was
  cancelled since. The dealer manager only: matrix row «Valuation use correction»
  (`valuation_use_reversal`, write manager-only; the row menu offers it only when the session
  may write it). Refused while a signed contract carries the valuation, cancelled included (409
  `signed_contract_carries_it`), when it is not stamped (409 `valuation_not_used`), and for a stamp
  younger than the nightly check's one-hour grace (409 `stamp_too_recent`, `CONFIRMATION_GRACE`: a
  confirmation stamps before it signs, and the lock holds only the valuation row; the row menu
  disables the entry with that reason), and **while a pending contract of the dealership carries
  it** (409 `pending_contract_carries_it`; KAN-520, Anto 2026-11-03, option A, read through
  `sales.public.valuations_carried_by_pending_contracts`): that contract's confirmation would find
  the stamp set, write nothing and release the valuation's lock before signing, so a clear in
  between would leave a signed contract on an unstamped valuation. The contract is cancelled
  first. A confirmation only runs on a contract already committed as pending, which leaves pending
  only in the commit that signs it, and the clear reads pending contracts **before** signed ones,
  so a signing committed between the two reads is still seen — the window is closed
  (`tests/test_valuation_revert_use.py` interleaves a clear with a re-confirmation, and a signing
  with each of the two reads). Audited
  (`valuation_use_reverted_by_hand`, before/after `usedAt`, the reason) and published as
  `valuation.valuation.use_reverted`, like the compensation.
- **Nightly reconciliation reports, never repairs, the stamp and Stock's pointer** (KAN-115,
  rule 10): a valuation stamped for over an hour that no signed contract of its dealership
  carries (`valuation/reconciliation.py` — a confirmation whose commit and compensation both
  failed, or a hand stamp from before KAN-115; the dealer manager clears either with
  «Verwendung aufheben», above — once a pending contract still carrying it is cancelled); a signed contract whose existing trade-in valuation of
  the same dealership is not stamped (`sales/reconciliation.py` — signed before KAN-101, or the
  compensation race; repaired by hand as above); a `stock_item.valuation_ref_id` naming no
  valuation, or a `valuation_ref_amount` that differs from its `final_offer`
  (`inventory/reconciliation.py`); a valuation whose `vehicle_vin` differs from the VIN of the
  vehicle its `vehicle_id` names (`valuation/reconciliation.py`, KAN-449 — rows written before
  the refusal). Nothing writes `used_at` from these checks.
- **Stock's `valuationRef`** on a trade-in (ADR-074) is set when inventory's
  `sales.contract.confirmed` consumer creates the pipeline item (`inventory/services/pipeline.py`,
  from `tradeIn.valuationId`, read through `valuation.public` in the same transaction) — the
  item does not exist when Sales confirms, so Sales never calls
  `inventory.public.set_valuation_ref` (which still has no production caller). The event's
  `tradeIn` block names the valuation by id, never its figure; Stock takes the figure from the
  valuation, never from the event (`pricingSnapshot.tradeInValue` is the deal's trade-in
  credit, which Stock's consumer ignores).
- **ADR-070** (amending FR-V-17): a *configuration* never writes vehicle-mdm; it attaches to a
  valuation. A valuation created **with** a `configurationId` (`record` mode only, KAN-10)
  defaults its vehicle fields from it (a field sent wins), references only the vehicle-mdm
  record the configuration already links, and creates none; `valuation.created` carries its
  `configurationId`, so Vehicle keeps a configuration a valuation took for good (KAN-270,
  `valuation.public.referenced_configuration_ids` the read-only safety net). **Its VIN is the configuration's**
  (Anto, 2026-10-25, KAN-449): a `vin` sent beside one that names a car (a VIN or a linked
  record) and differs is 422 `vin_differs_from_configuration`
  (`valuationCreate.vinDiffersFromConfiguration`), compared normalised on both sides, and the
  configuration's VIN is stored normalised; `ValuationCreateDialog` shows it read-only and sends
  none. A configuration with no VIN links no car (KAN-326), so a VIN typed beside it is stored
  and links none (Anto, 2026-10-26, KAN-449 option A). **No valuation and no trade-in creates a
  vehicle-mdm record** (Anto, 2026-10-31 option B, 2026-11-01 option A, KAN-189; ADR-070 and FR-S-08
  amended): created **without** a configuration, `create_valuation` stores the VIN and only *links*
  the record vehicle-mdm already holds for it (`vehicle.public.match_vehicle`, a read); the offer's or
  pending contract's VIN trade-in (`sales/services/trade_in.py::set_trade_in`) does the same, keeps
  `trade_in_vin`, allocates no owner/keeper party (the car shows on the customer's Vehicles tab once
  bought: Anto, 2026-11-02, option A — `.claude/rules/customer.md`) and finds an existing valid valuation by the linked record, else by VIN
  (`valuation.public.list_valid_valuations_for_vin`, compared normalised). A VIN-only trade-in counts
  as a trade-in (`priced_deal.has_trade_in`: the container, and `tradeIn` on
  `sales.contract.confirmed`), so it becomes a pipeline item whose purchase booking creates the record
  (ADR-103). A pre-1981 chassis number in `vin` (KAN-353, `.claude/rules/vehicle.md`) never finds or
  links a vehicle-mdm record, not even the configuration's. The record-mode
  trade-in path (configurator → valuation → the offer or pending contract references it,
  `.../trade-in/valuation`) is built (KAN-10, KAN-67).
