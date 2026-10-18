---
paths:
  - "app/customer/**"
  - "frontend/apps/dms/src/**/*ustomer*"
  - "frontend/apps/dms/src/**/*ustomer*/**"
  - "frontend/apps/dms/src/components/DuplicateWarningPanel.tsx"
  - "frontend/apps/dms/src/components/PhoneInput.tsx"
  - "app/core/postal_codes.py"
  - "frontend/apps/dms/src/hooks/useAdvisorOptions.ts"
  - "tests/test_customer*.py"
  - "scripts/*vehicle_party*.py"
  - "tests/test_*vehicle_party*.py"
  - "tests/test_legal_basis.py"
  - "tests/test_*preferred_channel*.py"
  - "alembic/versions/customer/**"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises PRD-Customers v2.2 (incl. its
§Conformance review, rulings D-03…D-26), ADR-014, ADR-030, ADR-064, ADR-065, ADR-067.
Verified against main@7a90ae4 on 2026-10-12 (every present-tense claim checked against the
code). Fix this file in the same PR as any change to what it states; /drift-audit re-checks it
weekly. -->

# Customers

## Ownership

- **Customers are owned by the dealer group** (ADR-014): exactly one customer record per
  group, opened and deduplicated group-wide. A standalone dealership is a group of one — one
  dedupe path, not two. The scope column is `group_id`, never `tenant_id`. Numbers come from a
  per-group sequence, allocated inside the caller's transaction (a rollback re-issues).
- Sharing a customer between sister dealerships shares it between separate legal entities
  (data controllers). The evidence record exists (`LegalBasis`, ADR-030, append-only;
  `enable_group_read` refuses without one); whether a real group may share data is the open
  legal question — raise it, do not decide it.

## Contact channels (ADR-067) — child records, not columns

- `customer_phone`, `customer_email`, `customer_address`: one row per value, each with a type,
  an optional free-text label, `isPrimary` with **exactly one primary per type-group**
  (enforced transactionally, on create and on every update/delete — re-elect when a primary
  goes), `validFrom`/`validTo`, a `doNotUse` flag with a reason, and **consent per channel**
  with its source and timestamp. All three update paths go through one helper,
  `_prepare_primary_change` in `app/customer/services/customer.py`.
- **Changing a row's type settles both type-groups** (KAN-102, Anto's ruling). The group it
  leaves re-elects. In the group it joins, `isPrimary: true` in the same PATCH makes the moved
  row primary; otherwise that group's existing primary stays and the moved row is demoted — a
  type change never silently demotes another row. Joining a group with no primary elects it.
  A row the same PATCH closes or flags `doNotUse` never takes the flag, `isPrimary: true` or not.
- A create elects its new row when the group has no **usable** primary (KAN-151), so a row
  added beside closed or `doNotUse` rows becomes primary without `isPrimary: true`; an existing
  usable primary is never demoted by a create that does not ask for it.
- Reopening a row (`validTo: null`, `doNotUse: false`) settles its type-group too (KAN-113): a
  group with no usable primary elects the reopened row; an existing usable primary keeps the
  flag. Usability is judged after the PATCH, so `isPrimary: true` on a row that is closed or
  `doNotUse` — by this PATCH or an earlier one — never unsets the group's usable primary.
- **A customer keeps at least one usable phone or email** (FR-03, ADR-067):
  `_assert_not_last_contact_point` refuses closing, flagging `doNotUse` or deleting a usable row
  when no other usable phone or email remains; a row already closed or `doNotUse` changes no
  usable count and is never refused (KAN-330).
- The six contact projections — `phoneMobile`, `phoneLandline`, `phoneWork`, `email`,
  `emailSecondary`, `address` (primary domicile) — are **read-model projections**, computed and
  never stored. Per type: the flagged primary among usable rows, else the oldest usable row,
  else null; `email` is personal, else work; `emailSecondary` is work when distinct. **Never
  add a flat column** — the cheap implementation is exactly what this decision removed.
- `consentScope` (marketing / invoicing / service; required on a new grant, NULL on a legacy row
  reads as marketing) and `consentSource` as an enum (form / counter / web / phone) — see FR-23.

## Rulings (all closed — do not re-open)

- **D-17 — a marketing send requires three gates:** `marketingConsent` (the legal basis under
  revDSG) · per-channel `consentGranted` with `consentScope = marketing` (read through
  `channel_authorises_marketing`; no caller yet — Marketing is not built) · `newsletter` (the
  subscription, one boolean, permanently).
- **D-20 — the address is optional at creation**; an address-less customer is gated at contract
  **confirmation**, not at the offer or at contract creation.
- **D-21 — `preferredChannel`** ∈ `email` / `phone` / `post` / `whatsapp`. The retired
  `message` value is still an enum member (old rows load) and the API still accepts it on write
  — only the UI omits it. Report such rows, never map them to `whatsapp`.
- **D-22** — salutation `herr` / `frau` / `firma` / `neutral`, for persons and businesses.
  Gender is independent (`female` / `male` / `other` / `unspecified`) — **never inferred from
  the salutation**.
- **D-23** — `website` is not business-only. **D-24** — `advisorId` defaults to the acting user
  on create only; nullable and clearable. **D-25** — the list offers Export and Print; there
  is **no bulk anonymise**. **D-26** — the field is `creditBlock`, with `creditBlockedAt`.
- **Legal form** is one of six codes (`ag`, `gmbh`, `einzelfirma`, `verein`, `genossenschaft`,
  `weitere`). Labels are meant to be localised; today they are hardcoded German in
  `customerOptions.ts` (KAN-104). SA, Sàrl and Sagl are the French/Italian *names* of AG and
  GmbH, not separate forms; the registered name keeps its own suffix.
- **Canton is derived server-side from the postal code (D-13)** and never accepted from the
  client. It is not a removable field: the list filter, a grid column and the audit trail
  depend on it.

## Blocks and flags

- **ADR-065 — a credit block stops contract confirmation, not the offer** (quoting a blocked
  customer is often how the block gets resolved). **Do-not-contact is a different fact** — the
  `lifecycleStatus` value `do_not_contact`, not a flag — and stops the offer and the contract.
- A credit block must be visible on the customer's own screens, not only inside the offer.
- **`vatStatusConfirmed`** (Anto, 2026-10-11, KAN-295): `vatRegistered = false` is also the
  default, so a second checkbox on the customer says someone checked it. Every row started
  unconfirmed. The legacy transaction import holds a trade-in from a non-registered,
  unconfirmed customer (it would book a fiktiver Vorsteuerabzug on a guess); live business
  does not read it yet.

## Vehicles and parties

- **ADR-064 — vehicle–customer links carry a role and are time-bounded.** Owner, keeper
  (Halter) and driver are different parties often enough that collapsing them loses what a
  seller needs. A new holder **closes** the previous row instead of overwriting it
  (`allocate_vehicle_party`). A customer merge re-points the duplicate's rows at the survivor;
  a row whose (vehicle, role, effective_from) the survivor already holds cannot be (the unique
  key spans `customer_id`) and is **kept** on the duplicate with `merged_into_customer_id`
  naming the survivor — never deleted (KAN-177, Anto's ruling 2026-10-12, option A): an open or
  future-closed copy is closed at its own start, audited as a `vehicle_party_remove` and
  published as `unlinked`; a copy already closed in the past keeps its `effective_to` and takes
  only the mark, listed by the merge's own audit event. **Not built:** the transfer on
  `finance.invoice.issued` (WP-9, KAN-74).
- Allocation resolves the vehicle against `vehicle_mdm` (global, ADR-022), never the frozen
  legacy `vehicle` table — `allocate_vehicle_party` does not look it up, so its callers resolve
  it first (the allocate routes 404 an unknown one; the trade-in on an offer or a pending
  contract, `set_trade_in`, creates or gets it) — and resolves the customer in the caller's
  group (404, never a cross-group link).
- **Holders are per dealer group** (Anto's ruling on KAN-99, 2026-10-04): the VIN is global
  (`vehicle_mdm`), but each group keeps its own owner/keeper/driver history for it.
  `allocate_vehicle_party` resolves the customer in the caller's group (404 otherwise — this
  covers the trade-in path too), then closes **every** open holder of that (vehicle, role) in
  the caller's group, found through `Customer.group_id`, in the **same transaction** as the
  insert — never another group's row. Concurrent allocations are serialised by a Postgres
  advisory lock on (vehicle, role, group): a row lock cannot do it, because a concurrent
  request never sees the row being inserted. Reads (`list_vehicle_parties`, and
  `list_vehicle_party_holders`, which also returns each holder's name for the Identity tab,
  KAN-140) are filtered the same way, through one shared statement: a name can only come from
  a holder the group filter already admits.
- **No path bypasses the close rule** (Anto, option A, KAN-148): a create whose `effectiveTo`
  is still to come is a holder now and goes through `allocate_vehicle_party` (it keeps its end
  date); one already past is history and closes nobody, but takes the allocation's locks and
  refuses a merged customer the same way (KAN-370). A PATCH whose `effectiveTo` leaves the
  row holding its role (`null` or still to come) takes the same advisory lock and closes every
  other open holder in the group; a reopen publishes `customer.vehicle_party.linked`, and a PATCH
  that ends a holding row (`effectiveTo` now or past) publishes `unlinked` (KAN-352). Disconnect
  locks and re-reads its row, so a race closes it once (one `unlinked`). Lock order is advisory,
  then row. The copy a customer merge kept (KAN-177) is never reopened (409 `merged_party_row`).
- **A customer merge takes the same lock** (KAN-356): before it reads or re-points a party row,
  `_repoint_vehicle_parties` takes the (vehicle, role, group) advisory lock for every pair the
  duplicate has a row in, in sorted order, then locks the rows — so it never interleaves with an
  allocation or a reopen of a pair it has seen. Then it locks the duplicate's customer row
  (`FOR NO KEY UPDATE`) and reads the pairs again. **Allocation and a reopen refuse a merged
  customer** (KAN-365): they read the customer row `FOR SHARE` under their advisory lock and answer
  409 `customer_merged` (with `mergedIntoCustomerId`) — after KAN-177's row-level
  `merged_party_row` check, which the kept copy still answers — so an allocation to the duplicate
  on a pair new to it either commits before that re-read (and is re-pointed) or waits for the
  merge and is refused. A pair that appears while it waits is only tried, never waited for (out of
  order, it could deadlock another merge): if it is held, the merge is refused, 409
  `merge_vehicle_party_busy`, and can be sent again.
- **A merge locks its survivor too** (KAN-372, `_lock_merge_customers`): after its advisory locks
  it locks both customer rows **in id order** — the duplicate `FOR NO KEY UPDATE`, the survivor
  `FOR SHARE` — and re-reads both statuses under them: a survivor that is merged (or whose own
  merge was in flight and committed) is refused, 409 `merge_target_merged`; a duplicate a
  concurrent merge already merged, 409 `customer_already_merged`. Lock order everywhere: advisory
  (sorted) → customer rows (id order) → party rows. Two merges in opposite directions ask for the
  two rows in the same order, so the second waits and is refused; a merge X→D racing D→T waits
  for D's row and is refused; allocation and a reopen take one customer row `FOR SHARE` after
  their advisory lock, which a merge's survivor lock never blocks. **Not covered:** a merge D→T
  run *after* X→D has committed leaves X's `duplicateOfCustomerId` naming merged D (the
  sequential chain — see the follow-up ticket filed with KAN-372).
- **A party row carries the vehicle's label, never a join** (KAN-84, rule 2):
  `vehicle_vin`, `vehicle_number`, `vehicle_make`, `vehicle_model`, `vehicle_model_year`,
  `vehicle_trim` and `vehicle_label_refreshed_at`, copied from
  `app.vehicle.public.get_vehicle_summaries` whenever a row is opened, created or repointed,
  and re-read nightly by `refresh_vehicle_party_labels` (`customer.vehicle_party_labels`), so a
  later catalogue match or VIN correction shows within a day (Anto's ruling, option A). A row
  without a label yet is filled for a read in memory only, and labelled by its next PATCH,
  re-confirm or Disconnect of an open row, or by the nightly job (a Disconnect of an
  already-closed row returns before committing and drops it).
  `tests/architecture/test_no_cross_context_mapping.py` forbids a relationship or FK back.
  A row whose `vehicle_id` names no vehicle-mdm record still renders — `vehicle.vin` and
  `vehicleNumber` are nullable in the read — and reconciliation reports it (KAN-339).
- **Cross-group closes and links written before KAN-99 are detected, not repaired** (KAN-139,
  KAN-176): `app.customer.reconciliation.find_cross_group_vehicle_party_closes` classifies every
  `vehicle_party_remove` audit row and `unlinked` outbox event, and
  `find_cross_group_vehicle_party_links` every `vehicle_party_add` row and `linked` event (the
  trade-in path passed a body `customerId` through unresolved), against the named customer's
  group; `scripts/detect_cross_group_vehicle_party_closes.py` runs both. A stamp of a
  dealership in that group is same-group: before WP-3 PR-2 (2026-08-25) customers were
  dealership-scoped, and the close was the D-12 hard-delete. Close-then-open dates from WP-5
  PR-9 (2026-08-28), so a genuine close finding falls between then and KAN-99. Read-only (a
  `READ ONLY` transaction) and not a nightly check (the audit log is append-only, so a finding
  never clears); a repair is Anto's decision.
- Group-wide customer history must be served from an event-built projection keyed by
  `group_id` (reporting), never by fan-out across dealerships × services (rule 11, two hops).
  **Not built:** `app/reporting` is a stub, and the History tab shows the audit log.

## Screens

List and detail render their actions from the one shared `customerRowMenu` (ADR-061). A
contact card offers `tel:` / `mailto:` links and marks the preferred channel.
