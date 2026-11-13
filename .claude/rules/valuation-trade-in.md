---
paths:
  - "app/sales/services/trade_in.py"
  - "app/sales/services/trade_in_seller.py"
  - "app/sales/services/contract.py"
  - "app/sales/services/contract_compensation.py"
  - "app/sales/services/valuation_carry.py"
  - "app/sales/reconciliation.py"
  - "app/valuation/services/contract_carry.py"
  - "app/valuation/reconciliation.py"
  - "app/inventory/services/pipeline.py"
  - "app/inventory/services/valuation.py"
  - "app/inventory/schemas/valuation.py"
  - "app/inventory/reconciliation.py"
  - "tests/test_sales_trade_in*.py"
  - "tests/test_kan189_trade_in_without_register.py"
  - "tests/test_trade_in_valuation_reconciliation.py"
  - "tests/test_valuation_contract_carry.py"
  - "tests/test_inventory_valuation_ref.py"
  - "frontend/apps/dms/src/components/deal-workspace/DealWorkspaceContainers.tsx"
  - "frontend/apps/dms/src/pages/OfferWorkspace.tradeIn*"
---
<!-- Maintainer note. ADR-047, ADR-066, ADR-070, ADR-074; FR-S-08. Moved out of valuation.md by
KAN-615 (2026-11-13) for the Sales and Stock files that use a valuation. Last verified against
main@73baf3f (2026-10-25). -->

# A valuation as a trade-in

- **A trade-in is a car** (Anto, KAN-574): a valuation without a VIN is refused as a trade-in —
  422 `trade_in_valuation_names_no_car` at attach, nothing written, whatever the deal names and
  configured or not — and
  never offered by `set_trade_in`'s FR-S-08 auto-attach. **Open with Anto on KAN-574:** a pre-1981
  chassis number is refused the same way (a trade-in takes a VIN until KAN-535); a deal that held
  such a valuation before keeps it (copy, contract and confirmation do not re-check).
- A valuation made **without a configuration** brings its car to a deal that names no trade-in car
  yet — its VIN, linked record and «Make Model Trim», or the VIN as the label without make or model
  (`sales/services/trade_in.py::_attach`); the confirmation publishes the label and Stock names the
  pipeline item by it. A deal that already names its car keeps it.
- **A VIN trade-in creates no vehicle-mdm record** (ADR-070, FR-S-08 amended): `set_trade_in` keeps
  `trade_in_vin`, only links the record vehicle-mdm holds, allocates no owner/keeper party (the car
  shows on the customer's Vehicles tab once bought, `customer.md`), and finds an existing valid
  valuation by the linked record, else by VIN (compared normalised). It counts as a trade-in
  (`priced_deal.has_trade_in`), so it becomes a pipeline item whose purchase booking creates the
  record (ADR-103). The record-mode path (configurator → valuation → the deal's
  `…/trade-in/valuation`) is built.
- **A contract consumes its trade-in valuation at confirmation**: `confirm_contract` calls
  `valuation.public.consume_valuation_for_contract` on its own session (ADR-047) **before**
  reserving the car, so a refusal never leaves a reservation to undo. **One valuation may back
  several contracts** (Anto) — the same VIN may be in pipeline twice, never twice in stock. Past
  `valid_until` it refuses the confirmation (`trade_in_valuation_expired`); a draft does not. If
  the reservation or the contract's commit fails, `revert_valuation_use` undoes "used" only when
  that confirmation set it and no other signed contract carries it; each compensation runs even if
  the other fails, and neither once the contract is signed or when the contract row cannot be
  locked (logged, left to the nightly run). **Cancelling a signed contract leaves it used**
  (ADR-066). **Only a signed deal uses a valuation.**
- **Valuation answers from its own `valuation_contract_carry` rows, never by asking Sales**
  (ADR-131): Sales' calls write them (`sales/services/valuation_carry.py`) — pending before a
  contract starting to carry it commits, signed after signing, released after a pending cancel or a
  replacement; signed is final (ADR-066).
- **Stock's `valuationRef`** (ADR-074) is set by inventory's own `sales.contract.confirmed`
  consumer when it creates the pipeline item, read through `valuation.public` in the same
  transaction — Sales never writes it (`inventory.public.set_valuation_ref` has no production
  caller). The event's `tradeIn` block names the valuation by id, never its figure; Stock takes the
  figure from the valuation (`pricingSnapshot.tradeInValue` is the deal's credit, ignored by Stock).
- **Nightly reconciliation reports, never repairs:** a stamp over an hour old that no signed
  contract of its dealership carries; a signed contract's unstamped trade-in valuation
  (`sales-reconciliation.md`); a `valuation_ref` naming no valuation or differing from its
  `final_offer`; a `vehicle_vin` differing from its vehicle's VIN; carry rows disagreeing with Sales.
  A manual figure is marked manual wherever it renders (`valuation.md`).
