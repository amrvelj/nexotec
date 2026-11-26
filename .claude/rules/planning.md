---
paths:
  - "app/planning/**"
  - "alembic/versions/planning/**"
  - "frontend/apps/dms/src/pages/PlannerResourcesPage*"
  - "frontend/apps/dms/src/components/plannerResources/**"
  - "frontend/apps/dms/src/modules/planning/**"
  - "frontend/packages/ui-kit/src/planner/**"
---
<!-- Maintainer note. ADR-211 (KAN-804, 2026-11-24), amending ADR-131. Summarises Decision A of
«Architecture decisions of slice 1» https://app.notion.com/p/4223e79334dd816e941bf780af822854 and
section 3.8 of «Build slice 1 - the order backbone» https://app.notion.com/p/4223e79334dd81d8b83bc1fbf4376c74
(binding; read the section before building against a line). Built by KAN-808 (backend) and KAN-809 (page).
Last verified against KAN-808 PR-2, cut from main@131a8f7 (2026-11-26). -->

# Planning — the planner every context books through

- **The thirteenth bounded context (ADR-211)**, directly above `platform`, below `integration`:
  core · platform · planning · integration · vehicle · contact · valuation · inventory/parts ·
  sales/aftersales · finance · reporting/compliance. Sales, Aftersales and Stock all book people,
  workplaces and cars; a planner inside one of the peers could not be called by the other, and
  `platform` is already the largest package. **`planning` calls `platform` only** — never
  `integration` (publishing to a connected calendar, if ever wanted, is `integration` consuming
  planning's events). `test_context_dependency_direction.py` enforces both directions.
- **Built (KAN-808 PR-1a; ADR-221):** the S7 lists (workplace types, technician types with rank and
  counted-time factor, qualifications — «mandatory» stored only) and the resources: a person (a user
  who works in the Betrieb, one per user per Betrieb, at one location; Anto 2026-11-25) or a workplace
  (a name unique at its location), with qualifications and weekly working hours (a second interval).
  API `api/resources.py` under `…/planning/`, router-level `aftersales` gate (T3), matrix row
  `planner_resources` (aftersales and the manager read, the manager writes), Dealer Administration
  for the activation gate. Deactivating or moving a resource marks its bookings to come (PR-1c).
  Every table carries `tenant_id` (T7); user and location are the three-column pattern, validated
  through `platform.public` (T5). A type copied from a starting set (ADR-222, PR-1d below) is a
  sample until changed or confirmed; the database refuses a rank or factor on one. A deactivated
  location takes no planner resource (422 `planning_location_inactive`, active or not). Exporter,
  reconciliation and chain (`9a5bf8ec0ed6`) registered.
- **Built (KAN-808 PR-1b; ADR-221 (4)):** the nightly label refreshes `planning.person_labels` (a
  person's user name) and `planning.location_labels` (every resource's location name),
  `services/labels.py`, through `platform.public`, before `reconciliation.run_all`, under the shared
  label rules (`events-worker.md`); gauges `planning.person` and `planning.location`.
- **Built (KAN-808 PR-1c; ADR-221 (1), (2), (6), (7)):** bookings (`planning_booking`, chain
  `2c3272f3d1c1`): **work** holds exactly one workplace and zero or more people, for a job on a car of
  a reference; a **drop-off/pick-up slot** any one or more resources for a reference, O7 long; a
  **block** one or more resources and an optional note (≤ 200), no reference (Anto, 2026-11-25).
  Half-open UTC intervals; released, never deleted. One write path, `services/bookings.py`: the
  per-Betrieb advisory lock first (`locks.lock_bookings`; no exclusion constraint), then the key's
  replay, the shape (422), the location active (422 `planning_location_inactive`), O6/O7 at the
  resources' location, `booking_rules.judge`, the write, the marks, the events, the commit; a refused
  write rolls its own transaction back. **O6** `planning.grid_minutes` (5–60 and a divisor of 60 —
  the store's `SettingDeclaration.divides`, 422 `setting_value_not_a_divisor`) binds work and slots,
  never a block; **O7** `planning.slot_minutes` (5–480, a multiple of O6 wherever both are in force —
  the store's `multiple_of`, `platform-settings.md`); both «not set» refuses (409
  `planning_setting_not_set`), Aftersales checklist items (`services/planner_settings.py`).
- **The rules (`services/booking_rules.py`):** refused 409 with `bookingIds` —
  `planning_car_at_another_workplace`, `planning_workplace_taken` (check 16: the same car at the same
  workplace is allowed), `planning_person_twice_on_job` (check 17; one person on two different jobs is
  allowed); the car is checked per Betrieb. Warned — `outside_working_hours` (check 18, split per
  local day; a person without hours gets none), `before_drop_off` (work starting before R's drop-off
  slot ends), `after_pick_up` (ending after R's pick-up slot starts), `resource_blocked` (Anto,
  2026-11-25: warned, never refused) — 422 `planning_warnings_unconfirmed` until `confirmed_warnings`
  holds every warning shown (`ConfirmedWarning`: code, resource, booking against — per warning, never
  per code, so one appearing between the two calls refuses again); an overruled warning is a
  `planning_overruled_warning` row (code, resource, the booking against, time booked, who with the name
  frozen, when) and an audit row `warning_overruled`.
- **«Neu einplanen»** (`replan_cause`): a block marks what it newly hits (`blocked`), a booked or
  moved slot the work of R it newly leaves outside (`outside_drop_off_pick_up`), deactivating a
  resource or moving it to another location (`resources.change_resource`, under the booking lock after
  the master-data lock) its bookings not yet ended (`resource_deactivated`, `resource_moved`; Anto,
  2026-11-25). What was already in the wrong place — booked knowingly — is not marked; a marked
  booking keeps its first cause; a block is never marked; a move clears the mark; a released block
  leaves its marks; booking in the past is allowed (Anto, 2026-11-25). `services/booking_marks.py`.
- **`planning.public`:** `book`, `move`, `release`, `block` (each commits its own transaction, ADR-047
  pattern B, and takes an idempotency key; a replay returns the booking as it now stands),
  `bookings_of_reference` (a read), `register_settings`, the frozen records (`BookingRecord`,
  `BookingOutcome` …) and enums. Events (`services/booking_facts.py`, one payload shape):
  `planning.booking.booked` (a block too), `.moved`, `.released`, `.marked_for_replanning`. The page's
  HTTP routes call the services inside planning (KAN-809); no other context calls `book`… yet, so
  none of them is in the ADR-047 lists.
- **Built (KAN-808 PR-1d; ADR-222): the starting sets.** Consumer
  `planning.starting_sets_on_module_granted` (`consumers.py`) of platform's
  `platform.module_entitlement.granted`: for `aftersales` only, `services/starting_sets.py` copies
  Nexotec's technician types (Lernender · Automobil-Fachmann · Automobil-Mechatroniker ·
  Automobil-Diagnostiker · Werkstattchef) and workplace types (Hebebühne · Arbeitsplatz ohne
  Hebebühne · Diagnoseplatz · Achsvermessung · Reifenmontage · Waschplatz; Anto, 2026-11-25) as
  samples with a seed code and no figure, named in the Betrieb's default correspondence language
  with the other three as translations (ruling 176). **Once per Betrieb, ever:** the marker
  `planning_starting_set` (chain `bfb2acffd59f`) is checked under the master-data lock, so a
  redelivery, a re-grant or a restatement copies nothing and a deleted sample never returns. A name
  the Betrieb already uses (case ignored) is skipped, its entry left its own. Audit
  `starting_set_copied` (no actor). A Betrieb that held Aftersales before the fact:
  `scripts/restate_module_entitlements.py --module aftersales` (platform's, dry run by default).
- **Built (KAN-808 PR-2): the S7 screen** `/administration/planner-resources` (`PlannerResourcesPage`,
  `components/plannerResources/`; nav entry shown with `aftersales`, capability `planner_resources` — read-only
  without the write). It embeds the shared `SettingRows` for O6 and O7 (the same rows as
  `/administration/settings/planning`; their `divides`/`multipleOf` hinted and checked, `platform-settings.md`), then
  the three lists — a name in the UI language from `nameTranslations`, else `name`; a sample badge with «Bestätigen»;
  delete offered disabled while a resource uses the entry — and the resources per location (filter): a person added
  from `/person-candidates` with the kit's `Picker`, a workplace by name; type, qualifications, sort order, deactivate
  (a confirm that says its bookings are marked) and reactivate; `personActsHere: false` marked «Arbeitet nicht mehr
  hier»; the working week a PUT with If-Match. Every versioned write sends If-Match; a refusal shows through
  `apiRefusalText`, a stale 409 with «Neu laden». The booking UI is the planner page (below).
- **Built (KAN-809; ADR-231): the planner page** `/planner` (`modules/planning`, sidebar «Planer» with
  `aftersales`), PL1: one Europe/Zurich day of one location (both in the URL), a row per person and per
  workplace in the kit's `ResourceDayGrid`, off-duty hatched, «neu einplanen» in words. API
  `api/bookings.py`, matrix row `planner` (ruling 180: aftersales and the manager read and write):
  `GET …/planning/day` (bookings not released holding a resource of the location, overlapping the day;
  O6 or null), `PATCH …/bookings/{id}` (move, If-Match, `confirmedWarnings`), `POST …/bookings/{id}/release`,
  `POST …/blocks`. PL2 changes and releases only — work and slots are booked from the order (KAN-812;
  Anto, 2026-11-26); a warning is overruled by re-sending exactly the warnings shown, and the dialog says
  it is recorded. PL4: people and/or workplaces, from, to, an optional note (no car, no weekly repeat).
  Times are Zurich's whatever the browser's zone (`plannerTime.ts`).
- **It owns:** resources (people, workplaces, bookable cars) with their types, qualifications and
  working hours; bookings and blocks; the overlap rule; the record of an overruled warning.
- **It owns no order, car or customer.** A caller's reference is the three-column pattern (id, label,
  `labelRefreshedAt`), passed in by the caller — the car of a work booking too; how its labels are
  refreshed is ADR-221 T5/T6 (not yet: planning cannot read a context above it).
- **Section 3.8 in short:** checks 16 and 17 refuse; check 18, the slots and a block warn; a block is
  always possible. Two people on one job, two jobs on one car at one workplace, hours planned above
  hours sold: allowed. No rule lives only in the frontend.
- Working hours move to Time & Absence once that is specified (ADR-211's revisit trigger).
