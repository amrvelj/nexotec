---
paths:
  - "app/planning/**"
  - "alembic/versions/planning/**"
---
<!-- Maintainer note. ADR-211 (KAN-804, 2026-11-24), amending ADR-131. Summarises Decision A of
«Architecture decisions of slice 1» https://app.notion.com/p/4223e79334dd816e941bf780af822854 and
section 3.8 of «Build slice 1 - the order backbone» https://app.notion.com/p/4223e79334dd81d8b83bc1fbf4376c74
(binding; read the section before building against a line). Built by KAN-808 (backend) and KAN-809 (page).
Last verified against main@32ad509 (2026-11-25). -->

# Planning — the planner every context books through

- **The thirteenth bounded context (ADR-211)**, directly above `platform`, below `integration`:
  core · platform · planning · integration · vehicle · contact · valuation · inventory/parts ·
  sales/aftersales · finance · reporting/compliance. Sales, Aftersales and Stock all book people,
  workplaces and cars; a planner inside one of the peers could not be called by the other, and
  `platform` is already the largest package. **`planning` calls `platform` only** — never
  `integration` (publishing to a connected calendar, if ever wanted, is `integration` consuming
  planning's events). `test_context_dependency_direction.py` enforces both directions.
- **Empty today** (KAN-804): `__init__.py`, an empty `public.py`, its import-linter contract
  (`pyproject.toml`, nothing to forbid yet — the first module goes into it), its rank, its own
  Alembic chain (branch root `8f0e01bb499e`, label `planning`), a `StubExporter`, and its place in
  every architecture test's context list.
- **It owns:** resources (people, workplaces, bookable cars) with their types, qualifications and
  working hours; bookings and blocks; the overlap rule; the record of an overruled warning.
- **It owns no order, car or customer.** A caller's reference is the three-column pattern (id, label,
  `labelRefreshedAt`), passed in by the caller — the car of a work booking too; labels are refreshed
  by events.
- **The rules (section 3.8), one service every entry point uses:** refused — the same car at two
  workplaces at one time, another car on a taken workplace, the same person twice on the same job at
  one time (checks 16, 17). Warned and bookable — outside the person's working hours, before the
  drop-off or after the pick-up; a booking made against a warning keeps the warning, the name and the
  time (check 18). A block is always possible; the bookings it hits are marked «neu einplanen». Two
  people on one job, two jobs on one car at one workplace, hours planned above hours sold: allowed.
- **`planning.public`** (KAN-808) offers book, move, release, block and «the bookings of reference
  R»; every change raises an outbox event. No rule lives only in the frontend.
- Working hours move to Time & Absence once that is specified (ADR-211's revisit trigger).
