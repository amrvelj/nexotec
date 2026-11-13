---
paths:
  - "app/customer/services/vehicle_part*.py"
  - "app/customer/services/vehicle_merge.py"
  - "app/customer/services/vehicle_page.py"
  - "app/customer/services/trade_in_holders.py"
  - "app/customer/services/stock_item_booking.py"
  - "app/customer/services/customer_merge.py"
  - "app/customer/models/vehicle_party.py"
  - "app/customer/models/stock_item_booking.py"
  - "app/customer/consumers.py"
  - "app/customer/reconciliation.py"
  - "app/customer/public.py"
  - "app/api/v1/customer_vehicles.py"
  - "app/api/v1/vehicle_parties.py"
  - "tests/test_*vehicle_party*.py"
  - "tests/test_customer_vehicle*.py"
  - "tests/test_customer_merge*.py"
  - "tests/test_customer_cross_group*.py"
  - "tests/test_customer_tag_merge_race.py"
  - "tests/test_vehicle_merge_holders.py"
  - "tests/test_kan566_former_holder_periods.py"
  - "tests/test_kan572_trade_in_storno_returned.py"
  - "tests/test_kan632_trade_in_facts_in_events.py"
  - "tests/test_kan644_kan583_handover.py"
  - "tests/test_kan657_attach_racing_customer_merge.py"
  - "tests/test_kan662_start_only_patch.py"
  - "tests/test_kan663_trade_in_lock_order.py"
  - "tests/test_kan673_party_write_follows_vehicle_merge.py"
  - "tests/test_kan392_merge_copy_audit.py"
  - "tests/test_kan680_vehicle_merge_party_rest.py"
  - "tests/test_kan674_backfill_trade_in_seller.py"
  - "scripts/*vehicle_party*.py"
  - "scripts/backfill_customer_stock_item_booking.py"
  - "frontend/apps/dms/src/components/customer-detail/Vehicles*"
  - "frontend/apps/dms/src/components/customer-detail/LinkVehicle*"
  - "frontend/apps/dms/src/components/customer-detail/vehicleLabel.ts"
  - "frontend/apps/dms/src/components/customer-detail/MergeCustomer*"
  - "frontend/apps/dms/src/utils/vehicleParty*"
  - "frontend/apps/dms/src/pages/*customerMerged*"
---
<!-- Maintainer note. ADR-022, ADR-064 (as amended 2026-10-13), rule 2, rule 12; Anto's rulings on
KAN-99, KAN-148, KAN-177, KAN-189, KAN-383, KAN-566, KAN-572, KAN-583, KAN-644. Moved out of
customer.md by KAN-615 (2026-11-13). Last verified against main@73baf3f (2026-10-25). -->

# Vehicle parties, trade-in holders and merges

## Holders (ADR-064)
- **Vehicle–customer links carry a role and are time-bounded**: owner, keeper (Halter) and driver
  are different parties. A new holder **closes** the previous row, never overwrites it
  (`allocate_vehicle_party`). **Not built:** the transfer on `finance.invoice.issued` (WP-9).
- Allocation resolves the vehicle against `vehicle_mdm` (global, ADR-022; its callers resolve it
  first) and the customer in the caller's group (404, never a cross-group link). A trade-in on a
  deal allocates no party: the car is linked once bought (below).
- **Holders are per dealer group** (Anto, KAN-99): the VIN is global, each group keeps its own
  history. An allocation closes **every** current holder of the (vehicle, role) in the caller's
  group — never another group's — in the insert's transaction, serialised by a Postgres advisory
  lock on (vehicle, role, group) (a row lock cannot: a concurrent request never sees the row being
  inserted). Reads share one group-filtered statement, so a name only comes from an admitted holder.
- **No path bypasses the close rule** (KAN-148): a create ending later goes through
  `allocate_vehicle_party`; one already ended is history — closes nobody, same locks, refuses a
  merged customer. A PATCH leaving a row holding takes the advisory lock and closes others; a
  reopen publishes `linked`, a PATCH ending a holding row now or earlier `unlinked`; a holding row's
  start-only PATCH is a reopen, minus its event. Disconnect re-reads its row (one `unlinked` per
  race). Lock order: advisory, then rows by id.
- **A future holder** (Anto, KAN-583, KAN-644): an allocation or reopen overlapping a holder whose
  start is still to come is 409 `vehicle_party_future_holder` and changes nothing. One from a later
  date closes the current holders at its start (sparing one ending first; no `unlinked`) and links
  them (`closed_for_party_id`): moving its start moves their end. «Eintrag beenden» on a planned row
  closes it at its own start; the rows it links hold until the next holder. An empty period never
  holds.
- **A party row carries the vehicle's label, never a join** (rule 2): copied from
  `vehicle.public.get_vehicle_summaries` when a row is opened, created or repointed; re-read on
  `vehicle.vehicle_mdm.summary_changed` (every row naming the vehicle, every group, locked in id
  order `FOR NO KEY UPDATE`, never from the payload) and nightly (`events-worker.md`; its batches
  lock in id order and wait for a held row — no SKIP LOCKED). A row without a label is filled
  in memory for a read only. A row whose `vehicle_id` names no record still renders and is
  reported.
- **Stock on the Vehicles tab is licensed with `sales`** (D-A-04): `stockItem` is filled only when
  `holds_entitlement(…, ModuleCode.SALES)` holds, else null. The customer router has no
  entitlement gate, so any inventory or sales data a customer route embeds needs the same check.

## Trade-ins (Anto, KAN-189 rulings 1–4, KAN-566, KAN-572)
- **A bought trade-in names its former owner:** `customer.trade_in_purchased` consumes
  `inventory.stock_item.purchased` and, for a trade-in's item, writes the contract's customer (from
  the event; an older event's through the worker, which asks Sales) as **owner and keeper, closed at
  the purchase** — the start of the purchase date in Switzerland, or the booking when that date is
  not after the signature or still to come — held since the signature. The customer's own rows join
  the period without gap or overlap (`test_kan566_former_holder_periods.py`); no other holder is
  touched; same locks and merged-customer refusal as an allocation, a customer merged since followed
  to its survivor. It lives in the customer context, so no write spans two contexts (rule 12).
- **A stornoed trade-in that went back to its customer reopens them:** only when the storno says
  `returnedToCustomer`, the same owner and keeper rows the purchase closed are reopened (audited,
  `linked`, under the allocation's locks, closing any other open holder); a role the customer holds
  now is left alone; without the flag the history stays. Delivery is unordered, so every purchase
  and storno is recorded first in `customer_stock_item_booking` (its consumers registered first)
  and the trade-in consumers judge by
  `occurredAt`: a purchase is recorded only while no later booking of the item is known, a returned
  storno a later purchase overtook reopens nobody, and a reopen leaves a role alone when a row of it
  recorded after the storno is open or ends after its recording, or a holder starts after the
  storno.
- **Accepted residual** (Anto, KAN-572 A; narrowed by KAN-632, disclosed in PR #316): a purchase
  consumed after its storno records no hand-over (after a *returned* storno the customer who got
  the car back is recorded nowhere); a returned storno consumed after a later booking reopens
  nobody; and two API-only cases (the SPA sends no party PATCH): an older row reopened by a PATCH
  after the storno is closed by a late returned storno, and a row recorded after the storno whose
  end a PATCH moved back before its own recording reads as history, so a late returned storno
  reopens over it.

## Merges
- **A merge re-points the duplicate's party rows**; a row whose (vehicle, role, effective_from) the
  survivor already holds is **kept** on the duplicate with `merged_into_customer_id`, never deleted
  (Anto, KAN-177): an open or future-closed copy is closed at its own start (audited, `unlinked`), a
  copy closed in the past only takes the mark. **Allocation and a reopen refuse a merged customer**
  (409 `customer_merged`), reading the customer row `FOR SHARE` under their advisory lock.
- **Lock order for a merge and an allocation:** advisory locks of every (vehicle, role, group) pair
  the duplicate holds, sorted → both customer rows in id order (duplicate `FOR NO KEY UPDATE`,
  survivor `FOR SHARE`, statuses re-read under them) → party rows. Exceptions: a reopen locks its
  party row before reading its customer row (still after the advisory lock), and the re-point of
  earlier merges. A pair that appears while a merge waits is only tried, never waited for — held,
  the merge is refused `merge_vehicle_party_busy` and can be sent again.
- **A merge re-points the earlier merges into its duplicate** (Anto, KAN-383): merging D into T
  sets every customer merged into D, and the copies naming D, to T in the same transaction, after
  holding D's row — "merged into" always names a live customer. The copies are only tried: one
  another writer holds refuses the merge. No event is published. Each re-pointed copy is audited on
  its customer (`vehicle_party_update`, reason `customer_merged`) and listed by the merge's audit
  (`vehiclePartyCopiesRepointedIds`; Anto, KAN-392 B). Chains written before are not repaired.
- A tag PATCH on the survivor racing a merge's moved tag is 409 `customer_tag_exists`, never a 500.
- **A vehicle merge moves party rows in a consumer** (`vehicle-mdm.md`); its kept copy is refused a reopen
  with 409 `vehicle_merged_party_row` (survivor's VIN); a PATCH and a Disconnect follow a merged record under
  the survivor's lock (KAN-680). The trade-in consumers take the locks of every record of the merge chain and
  read the customer's rows on all of them, so a fact consumed before the party move gives the same history
  (KAN-706, ADR-160 amended; `test_kan706_merge_before_party_move.py`).

## Detected, not repaired
- Cross-group closes and links written before holders were per group are detected
  (`find_cross_group_vehicle_party_closes` / `_links`, `scripts/detect_cross_group_vehicle_party_closes.py`),
  read-only and not a nightly check (the audit log is append-only, a finding never clears); a
  repair is Anto's decision.
