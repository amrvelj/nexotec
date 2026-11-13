---
paths:
  - "app/valuation/**"
  - "alembic/versions/valuation/**"
  - "tests/test_valuation*.py"
  - "tests/test_backfill_valuation_contract_carry.py"
  - "tests/valuation_carry_support.py"
  - "scripts/backfill_valuation_contract_carry.py"
  - "frontend/apps/dms/src/**/*aluation*"
  - "frontend/apps/dms/src/test/nameValuationCar.ts"
  - "frontend/packages/ui-kit/src/components/Valuation*Badge.tsx"
---
<!-- Maintainer note. PRD-Vehicles (FR-V-09, FR-V-17); ADR-029, ADR-048 as amended, ADR-066, ADR-070.
KAN-615 (2026-11-13) cut it to the invariants; what the confirmation, Sales and Stock do with a
valuation is valuation-trade-in.md. Last verified against main@73baf3f (2026-10-25). -->

# Valuations

- **`valuation` is its own bounded context** — "a dated commercial opinion, not a vehicle fact";
  the PRD's audit and retention rules for it are not implemented yet. Not under `vehicle` or
  `sales`.
- **Single writer.** Sales' `trade_in_valuation_id` and Stock's `valuation_ref_*` are read-only
  references to the same record.
- **Tenant-private even within a group** (ADR-029): a sister dealership never sees another's.
- **A standalone application**: creatable with no customer, no offer and no vehicle in the
  register; it carries a **validity period**. Licensed with `sales` (router-level gate, 404).
- **A vehicle carries a LIST of valuations** (ADR-048 as amended, ADR-066): the newest is current;
  older ones stay readable and are never edited or deleted (only `used_at` changes). «Neu
  bewerten» creates a new row whose `supersedesValuationId` names the old one (no read marks the
  old one superseded yet). A manual figure is **marked manual everywhere it renders**.
- **Status `draft → valid → expired`** (plus `used`) is **derived on read** from `is_draft`,
  `valid_until` and `used_at` — never stored, never repaired by a nightly job.
- **A valuation names its car** (Anto, KAN-574): a VIN (or a pre-1981 chassis number), a make and
  a model — each defaulted from the configuration when one is sent (a field sent wins, ADR-070),
  typed otherwise — else 422 `valuation_names_no_car`; rows written before keep what they had.
  **Open with Anto on KAN-574:** a VIN typed beside a configuration without one is stored, though
  the ruling asked the configuration to carry it.
- **ADR-070** — a configuration never writes vehicle-mdm; created **with** one (`record` mode
  only), a valuation defaults its vehicle fields from it, references only the record the
  configuration already links and creates none; `valuation.created` carries `configurationId`, so
  Vehicle keeps it. **Its VIN is the configuration's** (Anto, KAN-449): a different `vin` beside a
  configuration that names a car is 422 `vin_differs_from_configuration` (compared normalised);
  the dialog shows it read-only. A configuration with no VIN links no car, so a VIN typed beside
  it links none. **No valuation and no trade-in creates a vehicle-mdm record**: created without a
  configuration, the valuation stores the VIN and only *links* the record vehicle-mdm holds
  (`vehicle.public.match_vehicle`, a read). A pre-1981 chassis number never finds or links one.
- **The auto-i-dat figure is fetched, never typed** (Anto, KAN-552: «Wert auto-i-dat» is the
  Eintauschpreis, Einkaufs- and Verkaufspreis beside it; valuation calls are not rationed). «Bewertung
  von auto-i-dat holen» shows only for a configuration with a catalogue variant and the strict
  `valuation` capability (the probed `bewertung` row; never probed is not granted). It reads the car
  through `vehicle.public.get_provider_valuation_input`, asks `vehiclevaluation` then
  `residualvalue_table` under one correlation id, and stores a `ValuationProviderQuote`
  (tenant-private, written once). The valuation's `source` is **derived**: `auto_i_dat` with the
  quote's trade-in price when no other figure is sent, `manual` for the advisor's own figure with
  the quote beside it; a typed `auto_i_dat` without a quote, a quote of another configuration, or
  of another first registration or mileage is refused. Provider refusals are delivered as the
  provider worded them (ADR-044). Nothing asks the provider on opening a valuation. **Not
  verified against a real answer:** whether `vehicleExchangePrice` already includes the km
  correction (mock and fake transport only).
- **Provider quotes: no maximum age; unused ones go after ten years** (Anto, KAN-599 A): a quote a
  valuation references stays with it; an unreferenced one is deleted 10 years after `fetchedAt` by the
  daily `valuation.provider_quotes.purge_unused`, one audit row each (`provider_quote_retention.py`).
- **«Als verwendet markieren»** (`mark_used_by_hand`) repairs a missed stamp: only when a signed
  contract of the same dealership (cancelled included) carries the valuation, **whatever its
  status, expired included**; else `no_signed_contract`. `mark_used` is the one place that sets
  `used_at`, `revert_use` the one that clears it.
- **«Verwendung aufheben»** (Anto, KAN-253 option C; If-Match, a mandatory reason) clears a stamp
  no signed contract of the dealership stands behind. **The dealer manager only** (matrix row
  «Valuation use correction», `valuation_use_reversal`). Refused while a signed contract carries
  it (cancelled included), when it is not stamped, for a stamp younger than the nightly check's
  one-hour grace (a confirmation stamps before it signs), and **while a pending contract of the
  dealership carries it** (Anto, KAN-520: its confirmation would find the stamp set and sign over
  a cleared one) — one read of the carry rows, pending before its contract commits
  (`tests/test_valuation_revert_use.py`). Audited and published as
  `valuation.valuation.use_reverted`, like the compensation.
- **The valuation screens:** «Neu bewerten» is the detail's primary; the alternative, also in the
  grid's row menu (`valuationRowMenu.tsx`, ADR-061), is «Offerte aus dieser Bewertung erstellen»
  (Anto, KAN-569): a draft offer with the valuation's customer (none otherwise) and the valuation
  as trade-in, then the workspace — a draft valuation allowed; an expired or used one, or one
  naming no car, disabled with the reason; hidden without the `sales_offers` write; a refused
  later step keeps the screen, names the offer made, and a retry continues it.
  `ValuationCreateDialog` marks VIN, make and model required and holds its submit until they are
  filled.
