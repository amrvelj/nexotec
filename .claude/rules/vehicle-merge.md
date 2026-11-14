---
paths:
  - "app/vehicle/services/merge*.py"
  - "app/vehicle/services/stock_holding.py"
  - "app/customer/services/vehicle_merge.py"
  - "app/inventory/services/vehicle_merge.py"
  - "frontend/apps/dms/src/components/vehicle-detail/MergeVehicleDialog*"
  - "tests/test_vehicle_merge.py"
  - "tests/test_kan524_vehicle_merge.py"
  - "tests/test_kan680_vehicle_merge_party_rest.py"
  - "tests/test_kan706_merge_before_party_move.py"
---
<!-- Maintainer note. FR-V-12; split out of vehicle-mdm.md by KAN-524 (rule budget). Rulings:
KAN-524 (Anto, 2026-11-11 option A; 2026-11-13 Q1 B, Q2 A; 2026-11-14 Q3 A, Q4 A), KAN-634, KAN-644,
KAN-672, KAN-673, KAN-680, KAN-706; ADR-131, ADR-160. -->

# Merging two vehicle records (FR-V-12)

- The FR-V-12 merge (KAN-524; Anto: any user with the `vehicle_mdm` write merges any two records) is
  `POST /v1/vehicle-mdm/{duplicate}/merge` (`survivorId`, `survivorVersion`; If-Match the duplicate's;
  «Zusammenführen mit…», `MergeVehicleDialog`: compare, type the vehicle number that goes). Both rows
  locked in id order; refused with nothing written: the same record (422 `vehicle_merge_same_record`),
  a stale version, either merged (409 `vehicle_merged`), a live item of the **caller's** Betrieb on
  each (409 `vehicle_merge_both_in_stock`, both stock numbers; another Betrieb's never refuses — Q4 A).
  It changes no summary field and never calls customer or Stock (ADR-131): in its own transaction it
  re-points plates, accessories, odometer readings, custody events and `vehicle_stock_holding`, bumps
  the duplicate's version, audits `merge` and publishes `vehicle.vehicle_mdm.merged` (aggregate the
  duplicate, `tenant_id` null, payload `vehicleId` + `mergedIntoVehicleId`). Stock's
  `inventory.stock_items_on_vehicle_merged` moves every item on the duplicate (sold rows too) to the
  live record; one in stock takes its VIN, its configuration first on Vehicle's session
  (`follow_vehicle_merge_on_configuration`, audited `vin_corrected_by_merge`) — unless its Betrieb
  already holds the survivor live: it keeps its VIN and inventory reports «two live stock items of one
  Betrieb name one vehicle_mdm record». No Händlerschild assignment is written (removed until specified). Customer's
  `customer.vehicle_parties_on_vehicle_merged` (`app/customer/services/vehicle_merge.py`, KAN-634)
  then moves every party row naming the duplicate (every group's, open or closed) onto the survivor,
  followed to the live record via `vehicle.public.resolve_merged_vehicle_id` (delivery is unordered),
  under both cars' (vehicle, role, group) advisory locks (sorted), then both cars' rows in one id-ordered
  `FOR NO KEY UPDATE`, and relabels them. Where both cars bring a holder of a role in one dealer group
  (KAN-672; Anto, 2026-11-12, option C) the later start stays and the others are closed at its start
  (`_close_vehicle_party`: `vehicle_party_remove`, reason `vehicle_merged`, `unlinked`; equal starts: the
  survivor's row stays, 2026-11-13); a holder still to come ends the current one at its start, linked (KAN-644). A row
  whose (customer, role, start) the survivor already carries stays on the duplicate, closed at its start
  and marked `merged_into_vehicle_id`, as KAN-177's copy is (never reopened: 409 `vehicle_merged_party_row` with the survivor's VIN; the Vehicles tab marks it — Anto, KAN-680 A); where it
  still holds and ends later than the survivor's row, or not at all, that row first takes its end and
  its link to a planned holder (Anto, 2026-11-13: `vehicle_party_update`, `linked` on a reopen), then
  the role is settled. A copy that ended in the past changes nothing on the survivor.
  An allocation or a customer-side link naming a merged record is carried out on its survivor
  (KAN-673: `_lock_live_vehicle_role_in_group` resolves it, takes the survivor's lock and re-reads
  under it; a second lock, after a merge during the wait, is only tried — busy is 409
  `vehicle_merge_in_progress`; the response names the survivor). A PATCH or Disconnect whose row a merge
  moved while it waited takes the survivor's lock too (KAN-680). The trade-in purchase and storno consumers
  take the locks of every record of the merge chain and read and write the customer's rows on all of them —
  new rows on the live record, a row still on the duplicate where it stands, for the party move to carry
  (KAN-706, ADR-160 amended). Customer's reconciliation reports any other party row naming a merged vehicle.
