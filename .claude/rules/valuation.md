---
paths:
  - "app/valuation/**"
  - "app/sales/services/trade_in.py"
  - "app/sales/services/contract.py"
  - "app/inventory/services/pipeline.py"
  - "frontend/apps/dms/src/**/*aluation*"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises PRD-Vehicles (FR-V-09,
FR-V-17), ADR-029, ADR-048 as amended, ADR-066, ADR-070. Verified against main@568f416 on
2026-09-28 (every present-tense claim checked against the code). Fix this file in the same PR
as any change to what it states. -->

# Valuations

- **`valuation` is its own bounded context** — "a dated commercial opinion, not a vehicle
  fact", with its own audit and retention rules. Not under `vehicle` (vehicle identity is
  global, valuations are tenant-private) and not under `sales` (Sales owns the trade-in
  workflow, not the valuation record).
- **Single writer.** Sales and Stock hold a `valuationRef` and read the same record; there is
  never a second writer.
- **Tenant-private even within a group** (ADR-029): a sister dealership never sees another's
  valuations.
- **A vehicle carries a LIST of valuations** (ADR-048 as amended, ADR-066). The newest is
  current; older ones stay readable as superseded and are never edited or deleted. A manual
  figure is **marked manual everywhere it renders**.
- **A valuation is a standalone application**: creatable with no customer, no offer and no
  vehicle in the register. It carries a **validity period**.
- **Status `draft → valid → expired`** (plus `used` once a contract consumes it) is **derived
  on read** — never stored, never repaired by a nightly job.
- **A contract consumes its trade-in valuation at confirmation** (KAN-101):
  `sales/services/contract.py::confirm_contract` calls
  `valuation.public.consume_valuation_for_contract` on its own session (ADR-047), **before**
  reserving the car, so a refusal never leaves a reservation to undo. **One
  valuation may back several contracts** (Anto, 2026-09-29) — the same VIN may be in pipeline
  twice, never twice in stock. A valuation past `valid_until` refuses the confirmation
  (`trade_in_valuation_expired`); a draft does not. If the reservation or the contract's own
  commit fails, `revert_valuation_use` undoes "used" only when that confirmation set it and no
  other signed contract carries it; each compensating action runs even if the other fails.
  **Cancelling a signed contract leaves it used** (ADR-066).
- **Only a signed deal uses a valuation** (Anto, 2026-10-07, KAN-115). «Als verwendet
  markieren» (`POST /v1/valuations/{id}/mark-used`, `services/valuation.py::mark_used_by_hand`)
  repairs a missed stamp: allowed only when a signed contract (`signed_at` set, cancelled
  included) carries the valuation, **whatever its status, expired included** — the contracts
  signed before KAN-101 stamped nothing; refused otherwise (`no_signed_contract`). Sales answers
  through `sales.public.valuations_carried_by_signed_contracts`; `ValuationRead.hasSignedContract`
  carries the same answer to the row menu. `mark_used` is the one writer of `used_at`.
- **Stock's `valuationRef`** on a trade-in is set when inventory's `sales.contract.confirmed`
  consumer creates the pipeline item (`inventory/services/pipeline.py`, from
  `tradeIn.valuationId`) — the item does not exist when Sales confirms, so Sales never calls
  `inventory.public.set_valuation_ref` (which still has no production caller).
- **ADR-070** (amending FR-V-17): a *configuration* never writes vehicle-mdm; it attaches to a
  valuation. A valuation created **with** a `configurationId` (`record` mode only, KAN-10)
  takes its vehicle fields from it and creates no vehicle-mdm record; created **without** one,
  `create_valuation` still creates or gets the vehicle-mdm record when a VIN is given
  (`services/valuation.py`), and the offer's VIN trade-in (`sales/services/trade_in.py`) still
  creates the vehicle. Ending those two is Anto's call — read ADR-070 and ask; never change it
  in passing. The offer's record-mode trade-in path (configurator → valuation → the offer
  references it) is built (KAN-10).
