---
paths:
  - "app/vehicle/api/vehicle_mdm*.py"
  - "app/vehicle/api/vehicles.py"
  - "app/vehicle/api/lookup.py"
  - "app/vehicle/services/vehicle*.py"
  - "app/vehicle/services/vin_correction.py"
  - "app/vehicle/services/plate.py"
  - "app/vehicle/services/plate_handover.py"
  - "app/vehicle/services/plate_read_guard.py"
  - "app/vehicle/services/merge.py"
  - "app/vehicle/services/search.py"
  - "app/vehicle/services/lookup.py"
  - "app/vehicle/models/vehicle*.py"
  - "app/vehicle/models/plate.py"
  - "app/vehicle/consumers.py"
  - "app/vehicle/public.py"
  - "app/vehicle/__init__.py"
  - "app/vehicle/*/__init__.py"
  - "app/vehicle/reconciliation.py"
  - "app/vehicle/schemas/vehicle*.py"
  - "app/vehicle/schemas/lookup.py"
  - "app/core/validators.py"
  - "alembic/versions/vehicle/**"
  - "tests/test_vehicle.py"
  - "tests/test_vehicle_mdm*.py"
  - "tests/test_vehicle_plate*.py"
  - "tests/plate_support.py"
  - "tests/test_vehicle_merge.py"
  - "tests/test_vehicle_lookup.py"
  - "tests/test_vehicle_history.py"
  - "tests/test_vehicle_accessory_end_audited.py"
  - "tests/test_vehicle_party_label_on_summary_changed.py"
  - "tests/test_sales_invoice_plate_handover.py"
  - "tests/test_configuration_vin_repair.py"
  - "tests/architecture/test_plate_lookup_is_not_enumerable.py"
  - "tests/architecture/test_vehicle_write_services_have_a_caller.py"
  - "tests/architecture/test_shared_identity_lookup_response_shape.py"
  - "scripts/repair_configuration_vins.py"
  - "scripts/migrate_legacy_vehicles.py"
  - "frontend/apps/dms/src/**/*ehicle*"
  - "frontend/apps/dms/src/**/*ehicle*/**"
  - "frontend/apps/dms/src/utils/vin.ts"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises PRD-Vehicles, ADR-039 as amended,
ADR-043, ADR-045, ADR-085, ADR-114, ADR-119, ADR-121. Cut from vehicle.md by KAN-614 (2026-11-10) to
the invariants; vehicle.md was verified against main@73baf3f on 2026-10-25 - this cut was not
re-verified against the code. -->

# Vehicle master data (PRD-Vehicles)

## Identity
- **VIN is mandatory in vehicle-mdm.** A pre-VIN car is a pipeline stock item in `inventory`
  (ADR-045), promoted on VIN arrival by `inventory/services/pipeline.py::promote_and_flush`
  (idempotent per item; its one production caller is purchase booking, ADR-103).
- **Another context turns a VIN into a record only through `vehicle.public.resolve_vehicle_mdm_by_vin`**
  (KAN-337, `services/vin_resolution.py`): Vehicle's own session, a lost insert race resolved to the
  winner's record (KAN-257), plain data back. No `app/` module outside `app/vehicle` names
  `create_or_get_vehicle_mdm` or copies the race predicate (`test_vin_resolution_is_vehicles.py`).
- **A typed VIN is one spelling in every write** (FR-V-15): upper-cased, whitespace removed —
  `app.core.validators.NormalisedVin` (stock, trade-in; then ISO 3779) or
  `NormalisedVinOrChassisNumber` (valuation, configuration); the SPA mirrors it (`utils/vin.ts`).
  vehicle-mdm's own fields take `Vin`, which refuses an unnormalised value rather than fixing it.
  The shared lookup and the list search normalise their query the same way; the lookup answers 404
  for a value that is no VIN — a read, never 422. A refused VIN is named on screen
  (`common.invalidVin`).
- **A pre-1981 chassis number** (Anto): 1–17 letters and digits, accepted on a valuation or
  configuration only with a first registration before 1981-01-01 — on a configuration PATCH the
  stored VIN and date count for whichever the PATCH leaves out (else 422
  `chassis_number_requires_pre_1981_registration`); stored and printed normalised like a VIN — one
  spelling per car — and **never** finds, creates or links a vehicle-mdm record
  (`is_chassis_number`). `scripts/repair_configuration_vins.py` (dry run unless `--commit`) cleans
  VINs stored before the rule, once per environment (a pre-1981 car stored in another spelling is
  KAN-502's open question). A valuation's stored VIN is never edited.
- **A licence plate is never an identifier** — Wechselschild, reassignment, cantonal changes.
  `vehicle_plate` is a child table with validity dates and a `plate_group_id`; an ambiguous lookup
  shows a picker and never guesses.
- Vehicle identity is global; everything licensed about it is not (`vehicle-gateway.md`).
- **The shared identity lookup carries master data for every dealer** (ADR-043, ADR-112, ADR-075):
  `GET /v1/vehicle-mdm/lookup` answers `catalogueVariantId`, `listPrice` and `specification` for a
  known car, with or without a provider contract — never options, colours, tyres or images
  (`test_shared_identity_lookup_response_shape.py`, `test_shared_lookup_carries_master_data.py`).

## The Kontrollschild register is the dealer group's (ADR-039 as amended)
- `vehicle_plate.group_id`; every read and write in `services/plate.py` names the caller's group
  from the token — search, the Plates tab, the lookup's `currentPlate` and the trade-in match
  (`vehicle.public.match_vehicle(group_id=…)`) never see another group's plate (`plate.py` is on
  the group-read allow-list). Reconciliation checks the group against the recording Betrieb's.
- A plate is stored in one spelling (`normalise_kontrollschild`); «current» is the **Swiss** day
  (`plate.swiss_today`), never UTC.
- Recording: a plate current on another car of the group is 409 `plate_current_on_other_vehicle`
  with that car in `details.holders`; **naming that car is a plate read** (the guard below, purpose
  `plate_recording_refused`; past the limit `holdersWithheld`). The answer is the same POST with
  `moveFrom` (the listed assignments with their versions; each ends the day before) or
  `wechselschildWith` (one other car; a group of at most two), serialised per plate by an advisory
  lock. Rows are never deleted — «beenden» ends one under If-Match.
- A sold car's plate is entered at Sales' «Rechnung anfordern», checked by
  `vehicle.public.check_plate_handover` and recorded by the consumer
  `vehicle.plate_on_invoice_requested`; a refusal there is raised — retried, then dead-lettered. The
  incumbent-DMS import (FR-V-08) is not built.
- Audit rows of a plate write carry validity and plate group, **never the plate value**. Every write
  service in `app/vehicle/services` has a production caller
  (`test_vehicle_write_services_have_a_caller.py`).

## Plate reads are audited and limited per user (ADR-085)
- Only through `services/plate_read_guard.py::read_plate_history`, never
  `plate.list_plates_for_vehicle` directly; no read of `VehiclePlate` outside `plate.py` /
  `plate_read_guard.py` except the named uses that hand out no plate
  (`test_plate_lookup_is_not_enumerable.py`). Every read and refusal writes an `audit_event`, never
  the plate values.
- Past `plate_read_limit` distinct cars per `plate_read_window_seconds` (`app/core/config.py`;
  platform-wide by design, **never** a Betrieb setting) the Plates tab is 403
  `plate_read_limit_reached`, a search hit `currentPlateWithheld`; the car page still opens. The
  shared lookup (ADR-043) answers `currentPlate: null` — its shape has no withheld flag.

## Changes and audits
- **A changed label is an event** (ADR-119): `update_vehicle_mdm` publishes
  `vehicle.vehicle_mdm.summary_changed` in its own transaction whenever a field
  `vehicle.public.VehicleSummary` carries changes — and only then (a Stammnummer, a status, a first
  registration in the same year, a refused write publish nothing); payload `vehicleId`, `version`,
  `changedFields` and the whole `summary`; `tenant_id` null (vehicle-mdm is global). Nothing writes a
  record's `catalogue_variant_id` after creation yet. Any new writer of a summary field calls `publish_summary_changed_if_changed` with the
  summary read before its write. **Ruled exception:** a brand rename publishes nothing; customers'
  Vehicles tabs catch up by the nightly label refresh (Anto;
  `test_a_brand_rename_reaches_the_vehicles_tab_the_next_morning`).
- **A bought car's VIN is corrected from Stock** (KAN-557): `vehicle.public.correct_vehicle_vin`
  (`services/vin_correction.py`) — the rules are in `inventory-vin-correction.md`.
- Every identity PATCH writes an `update` audit row (changed fields before/after, camelCase; none
  for a no-op). `"vin": null` or `"vehicleStatus": null` is 422; a duplicate VIN or Stammnummer is a
  409 naming the field — any other integrity error is not reported as a duplicate.
- **Any Betrieb may end a recorded accessory** (the car is shared; Anto): `validTo` = today in
  Switzerland, audited under the ending Betrieb and, when another recorded it, under that one too;
  the row is never deleted (FR-V-13).
- The FR-V-12 merge (`services/merge.py`, no caller yet — KAN-524) changes no summary field and
  never calls customer (ADR-131): in its own transaction it re-points plates, accessories, odometer
  readings and custody events, audits `merge` and publishes `vehicle.vehicle_mdm.merged` (aggregate
  the duplicate, `tenant_id` null, payload `vehicleId` + `mergedIntoVehicleId`). Customer's
  `customer.vehicle_parties_on_vehicle_merged` (`app/customer/services/vehicle_merge.py`, KAN-634)
  then moves every party row naming the duplicate (every group's, open or closed) onto the survivor,
  followed to the live record via `vehicle.public.resolve_merged_vehicle_id` (delivery is unordered),
  under both cars' (vehicle, role, group) advisory locks (sorted), then both cars' rows in one id-ordered
  `FOR NO KEY UPDATE`, and relabels them. Where both cars bring a holder of a role in one dealer group
  (KAN-672; Anto, 2026-11-12, option C) the later start stays and the others are closed at its start
  (`_close_vehicle_party`: `vehicle_party_remove`, reason `vehicle_merged`, `unlinked`; equal starts: the
  survivor's row stays, 2026-11-13); a holder still to come ends the current one at its start, linked (KAN-644). A row
  whose (customer, role, start) the survivor already carries stays on the duplicate, closed at its start
  and marked `merged_into_vehicle_id`, as KAN-177's copy is (never reopened: 409 `merged_party_row`); where it
  still holds and ends later than the survivor's row, or not at all, that row first takes its end and
  its link to a planned holder (Anto, 2026-11-13: `vehicle_party_update`, `linked` on a reopen), then
  the role is settled. A copy that ended in the past changes nothing on the survivor.
  An allocation or a customer-side link naming a merged record is carried out on its survivor
  (KAN-673: `_lock_live_vehicle_role_in_group` resolves it, takes the survivor's lock and re-reads
  under it; a second lock, after a merge during the wait, is only tried — busy is 409
  `vehicle_merge_in_progress`; the response names the survivor). Customer's reconciliation reports any other party
  row naming a merged vehicle.
- **Vehicle never reads or writes `VehicleParty`** (KAN-634, ADR-131 (5)): the Vehicle 360's
  «Allocate to customer» (`POST /v1/vehicle-mdm/{id}/allocate`) and holders
  (`GET /v1/vehicle-mdm/{id}/party-roles`) are served from the composition root
  `app/api/v1/vehicle_parties.py`, same paths and shapes; no `app/vehicle` module outside
  `reconciliation.py` (ADR-107) imports `app.customer`.
