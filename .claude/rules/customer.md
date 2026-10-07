---
paths:
  - "app/customer/**"
  - "frontend/apps/dms/src/**/*ustomer*"
  - "frontend/apps/dms/src/**/*ustomer*/**"
  - "frontend/apps/dms/src/components/DuplicateWarningPanel.tsx"
  - "frontend/apps/dms/src/components/PhoneInput.tsx"
  - "app/core/postal_codes.py"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises PRD-Customers v2.2 (incl. its
§Conformance review, rulings D-03…D-26), ADR-014, ADR-064, ADR-065, ADR-067. Verified against
main@568f416 on 2026-09-27; the vehicle-party lines against KAN-99's branch on
2026-10-04, the cross-group close detection against KAN-139's branch on 2026-10-06. Fix this file in the same PR as any change to what it states;
/drift-audit re-checks it weekly. -->

# Customers

## Ownership

- **Customers are owned by the dealer group** (ADR-014): exactly one customer record per
  group, opened and deduplicated group-wide. A standalone dealership is a group of one — one
  dedupe path, not two. The scope column is `group_id`, never `tenant_id`. Numbers come from a
  per-group sequence, allocated inside the caller's transaction (a rollback re-issues).
- Sharing a customer between sister dealerships shares it between separate legal entities
  (data controllers). A revDSG legal basis is needed before a real multi-dealership group
  holds data — raise it, do not decide it.

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
- **Not yet enforced:** reopening a row (`validTo: null`, `doNotUse: false`) in a group with no
  usable row leaves that group without a primary; the projection's oldest-usable fallback
  covers the grid. KAN-113.
- The grid's `Mobile` / `Email` / `Work phone` are **read-model projections**, computed and
  never stored. Fallback: the flagged primary among usable rows, else the oldest usable row,
  else null. **Never add a flat column** — the cheap implementation is exactly what this
  decision removed.
- `consent.scope` (marketing / invoicing / service) and `consentSource` as an enum
  (form / counter / web / phone) — see FR-23.

## Rulings (all closed — do not re-open)

- **D-17 — a marketing send requires three gates:** `marketingConsent` (the legal basis under
  revDSG) · per-channel `consent.scope = marketing` · `newsletter` (the subscription, one
  boolean, permanently).
- **D-20 — the address is optional at creation**; an address-less customer is gated at the
  **contract**, not the offer.
- **D-21 — `preferredChannel`** ∈ `email` / `phone` / `post` / `whatsapp`. The retired
  `message` value has no clean target: report such rows, never map them to `whatsapp`.
- **D-22** — salutation `herr` / `frau` / `firma` / `neutral`, for persons and businesses.
  Gender is independent (`female` / `male` / `other` / `unspecified`) — **never inferred from
  the salutation**.
- **D-23** — `website` is not business-only. **D-24** — `advisorId` defaults to the acting user
  on create only; nullable and clearable. **D-25** — the list offers Export and Print; there
  is **no bulk anonymise**. **D-26** — the field is `creditBlock`, with `creditBlockedAt`.
- **Legal form** is one of six codes (`ag`, `gmbh`, `einzelfirma`, `verein`, `genossenschaft`,
  `weitere`) with localised labels. SA, Sàrl and Sagl are the French/Italian *names* of AG and
  GmbH, not separate forms; the registered name keeps its own suffix.
- **Canton is derived server-side from the postal code (D-13)** and never accepted from the
  client. It is not a removable field: the list filter, a grid column and the audit trail
  depend on it.

## Blocks and flags

- **ADR-065 — a credit block stops the contract, not the offer** (quoting a blocked customer
  is often how the block gets resolved). **Do-not-contact is a different flag** and stops both.
- A credit block must be visible on the customer's own screens, not only inside the offer.

## Vehicles and parties

- **ADR-064 — vehicle–customer links carry a role and are time-bounded.** Owner, keeper
  (Halter) and driver are different parties often enough that collapsing them loses what a
  seller needs. A new holder **closes** the previous row instead of overwriting it — including
  the transfer on `finance.invoice.issued`.
- Allocation resolves against `vehicle_mdm`, never the frozen legacy `vehicle` table, and must
  check that both sides belong to the caller's group (404, never a cross-group link).
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
- **A party row carries the vehicle's label, never a join** (KAN-84, rule 2):
  `vehicle_vin`, `vehicle_number`, `vehicle_make`, `vehicle_model`, `vehicle_model_year`,
  `vehicle_trim` and `vehicle_label_refreshed_at`, copied from
  `app.vehicle.public.get_vehicle_summaries` whenever a row is opened, created or repointed,
  and re-read nightly by `refresh_vehicle_party_labels` (`customer.vehicle_party_labels`), so a
  later catalogue match or VIN correction shows within a day (Anto's ruling, option A). A row
  without a label yet is filled for a read in memory only, and labelled by its next PATCH,
  DELETE or re-confirm, or by the nightly job.
  `tests/architecture/test_no_cross_context_mapping.py` forbids a relationship or FK back.
- **Cross-group closes written before KAN-99 are detected, not repaired** (KAN-139):
  `app.customer.reconciliation.find_cross_group_vehicle_party_closes` (run by
  `scripts/detect_cross_group_vehicle_party_closes.py`) classifies every
  `vehicle_party_remove` audit row and `unlinked` outbox event against the closed customer's
  group. A stamp of a dealership in that group is same-group: before WP-3 PR-2 (2026-08-25)
  the action was the D-12 hard-delete, stamped with the dealership. Close-then-open dates from
  WP-5 PR-9 (2026-08-28), so a genuine finding falls between then and KAN-99. Read-only (a
  `READ ONLY` transaction) and not a nightly check (the audit log is append-only, so a finding
  never clears); a repair is Anto's decision.
- Group-wide customer history must be served from an event-built projection keyed by
  `group_id` (reporting), never by fan-out across dealerships × services (rule 11, two hops).
  **Not built:** `app/reporting` is a stub, and the History tab shows the audit log.

## Screens

List and detail render their actions from the one shared `customerRowMenu` (ADR-061). A
contact card offers `tel:` / `mailto:` links and marks the preferred channel.
