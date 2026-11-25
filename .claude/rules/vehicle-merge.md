---
paths:
  - "app/vehicle/services/merge*.py"
  - "app/vehicle/services/stock_holding.py"
  - "app/contact/services/vehicle_merge.py"
  - "app/inventory/services/vehicle_merge.py"
  - "app/vehicle/services/vin_correction.py"
  - "app/vehicle/services/vin_resolution.py"
  - "app/vehicle/api/vehicle_mdm.py"
  - "app/contact/services/vehicle_party_locks.py"
  - "app/contact/services/trade_in_holders.py"
  - "frontend/apps/dms/src/modules/vehicle/components/vehicle-detail/MergeVehicleDialog*"
  - "tests/vehicle/test_vehicle_merge.py"
  - "tests/vehicle/test_kan524_vehicle_merge.py"
  - "tests/contact/test_kan680_vehicle_merge_party_rest.py"
  - "tests/contact/test_kan706_merge_before_party_move.py"
  - "tests/contact/test_kan673_*.py"
  - "tests/contact/test_vehicle_merge_holders.py"
  - "tests/cross_context/test_kan723_*.py"
  - "tests/inventory/test_kan724_*.py"
  - "tests/inventory/test_kan729_*.py"
  - "tests/vehicle/test_kan730_*.py"
  - "tests/vehicle/test_kan733_*.py"
---
<!-- Maintainer note. FR-V-12; split out of vehicle-mdm.md by KAN-524 (rule budget). Rulings:
KAN-524 (Anto, 2026-11-11 option A; 2026-11-13 Q1 B, Q2 A; 2026-11-14 Q3 A, Q4 A), KAN-634, KAN-644,
KAN-672, KAN-673, KAN-680, KAN-706, KAN-724, KAN-733; ADR-131, ADR-160, ADR-182. Last verified against
main@32ad509 (2026-11-25). -->

# Merging two vehicle records (FR-V-12)

- The FR-V-12 merge (KAN-524; Anto: any user with the `vehicle_mdm` write merges any two records) is
  `POST /v1/vehicle-mdm/{duplicate}/merge` (`survivorId`, `survivorVersion`; If-Match the duplicate's;
  «Zusammenführen mit…», `MergeVehicleDialog`: compare, type the vehicle number that goes). An already-merged
  record is refused (409 `vehicle_merged`) on an unlocked read before any lock (KAN-733, ADR-182: no lock
  cycle with «VIN korrigieren»), then both rows locked in id order and checked again; refused with nothing written: the same record (422 `vehicle_merge_same_record`),
  a VIN record into a chassis-number one (422 `vehicle_merge_vin_into_chassis_number`, KAN-535; the reverse is allowed),
  a stale version, either merged (409 `vehicle_merged`), a live item of the **caller's** Betrieb on
  each (409 `vehicle_merge_both_in_stock`, both stock numbers; another Betrieb's never refuses — Q4 A).
  It changes no summary field and never calls contact or Stock (ADR-131): in its own transaction it
  re-points plates, accessories, odometer readings, custody events and `vehicle_stock_holding`, bumps
  the duplicate's version, audits `merge` and publishes `vehicle.vehicle_mdm.merged` (aggregate the
  duplicate, `tenant_id` null, payload `vehicleId` + `mergedIntoVehicleId`). Stock's
  `inventory.stock_items_on_vehicle_merged` moves every item on the duplicate (sold rows too) to the
  live record; one in stock takes its VIN, its configuration first on Vehicle's session
  (`follow_vehicle_merge_on_configuration`, audited `vin_corrected_by_merge`) — unless its Betrieb
  already holds the survivor live: it keeps its VIN and inventory reports «two live stock items of one
  business name one vehicle_mdm record» (a chassis-number car stays on the duplicate: one per record
  and Betrieb is an index; KAN-706's check names it, and Stock's daily `inventory.merged_record_items.repair`
  moves it once nothing blocks it, KAN-724); one that took a VIN publishes
  `inventory.stock_item.vin_corrected`. No Händlerschild assignment is written (removed until specified). Contact's
  `contact.vehicle_parties_on_vehicle_merged` (`app/contact/services/vehicle_merge.py`, KAN-634)
  then moves every party row naming the duplicate (every group's, open or closed) onto the survivor,
  followed to the live record via `vehicle.public.resolve_merged_vehicle_id` (delivery is unordered),
  under both cars' (vehicle, role, group) advisory locks (sorted), then both cars' rows in one id-ordered
  `FOR NO KEY UPDATE`, and relabels them. Where both cars bring a holder of a role in one dealer group
  (KAN-672; Anto, 2026-11-12, option C) the later start stays and the others are closed at its start
  (`_close_vehicle_party`: `vehicle_party_remove`, reason `vehicle_merged`, `unlinked`; equal starts: the
  survivor's row stays, 2026-11-13); a holder still to come ends the current one at its start, linked (KAN-644). A row
  whose (contact, role, start) the survivor already carries stays on the duplicate, closed at its start
  and marked `merged_into_vehicle_id`, as KAN-177's copy is (never reopened: 409 `vehicle_merged_party_row` with the survivor's VIN; the Vehicles tab marks it — Anto, KAN-680 A); where it
  still holds and ends later than the survivor's row, or not at all, that row first takes its end and
  its link to a planned holder (Anto, 2026-11-13: `vehicle_party_update`, `linked` on a reopen), then
  the role is settled. A copy that ended in the past changes nothing on the survivor.
  An allocation or a contact-side link naming a merged record is carried out on its survivor
  (KAN-673: `_lock_live_vehicle_role_in_group` resolves it, takes the survivor's lock and re-reads
  under it; a second lock, after a merge during the wait, is only tried — busy is 409
  `vehicle_merge_in_progress`; the response names the survivor). A PATCH or Disconnect whose row a merge
  moved while it waited takes the survivor's lock too (KAN-680). The trade-in purchase and storno consumers
  take the locks of every record of the merge chain and read and write the contact's rows on all of them —
  new rows on the live record, a row still on the duplicate where it stands, for the party move to carry
  (KAN-706, ADR-160 amended). Contact's reconciliation reports any other party row naming a merged vehicle.
- **A merged duplicate's VIN never links the dead record** (ADR-160 as amended, KAN-723): a
  configuration's VIN and the trade-in/valuation match follow the merge (`live_vehicle_mdm`); the
  purchase booking and «VIN korrigieren» (KAN-729) are 409 `vin_names_merged_vehicle` with the survivor's number and VIN —
  except «VIN korrigieren» on that survivor itself: it takes the VIN over and the dead record takes the old one (KAN-730).
