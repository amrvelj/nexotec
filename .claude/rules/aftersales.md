---
paths:
  - "app/aftersales/**"
  - "app/parts/**"
---
<!-- Maintainer note. ADR-211 to ADR-215 (KAN-804, 2026-11-24). Summarises the Aftersales PRD page
«Build slice 1 - the order backbone» (version 0.4, approved by Anto 2026-11-24)
https://app.notion.com/p/4223e79334dd81d8b83bc1fbf4376c74 and «Architecture decisions of slice 1»
https://app.notion.com/p/4223e79334dd816e941bf780af822854. The slice page is binding; read the section
behind a line before building against it. Its tickets: KAN-804 to KAN-818.
Last verified against main@32ad509 (2026-11-25). -->

# Aftersales (workshop orders) and Parts

- **`aftersales` is an empty shell until KAN-810; `parts` stays empty in slice 1.** The slice is one
  road: an order for a customer's car, jobs and positions, the customer's yes at the desk, the work
  planned, «Fahrzeug ist da», the technician's clock, the quality check, «Rechnungsbereit», the
  handover. Parts, offers and the customer's page, findings, other payers, internal orders and
  counter sales are later slices (section 2) — do not build them in.
- **Money (ADR-212, ADR-215).** A workshop order shows **net and gross per position and one VAT line
  per payer** (Anto's ruling 4) — ADR-057's gross-only rule is for vehicle sales. Totals come from the
  one money calculation in `app/core` (KAN-807): per payer the net sum, VAT once on the sum, rounded to
  the Rappen, then the Betrieb's cash rounding as its own line «Rundung»; VAT is never changed by the
  rounding. A typed price leads: typed net, gross follows; typed gross, net follows; the saved price is
  the typed one, up to four decimals per unit. `aftersales` and `parts` are the only contexts that may
  declare a net amount (`test_net_line_only_where_ruled.py`). **There is no `vatTreatment`** here either.
- **Labour (ruling 183, section 10).** A labour amount is **hours × the hourly rate**, whatever unit
  the Betrieb shows (hours, or work units AW with its work units per hour); a rate per work unit is
  shown and never calculated with. A position keeps the unit and the AW-per-hour it was entered with.
  Rates are Platform master data (D-A-19, KAN-806): Aftersales asks «which rate applies here»
  (agreed rate with the invoice recipient first, else the category rate at the order's location on
  the order's date) and keeps the source.
- **The stay of the car (ADR-213, Decision C).** `aftersales` owns a «stay»: one presence of a car at
  a Betrieb, arrival and departure. Orders point to a stay; one running stay per car and Betrieb; a
  second order for a car in the house joins its stay. **Every departure, whichever screen records
  it, is one service function** that checks the stay's orders and the time against the arrival (later
  also the «nicht verkehrssicher» mark, which is aftersales data per vehicle and Betrieb, never a
  field of the shared vehicle record). The **workshop status is worked out** from the stay, the
  clocks, the positions and the check — never set by hand, never a reception field or a status
  column on the order; lists read a projection of it. The mileage read at arrival or departure goes
  to `vehicle` as an odometer reading through an event (source «service visit»).
- **Settings (ADR-214, Decision D).** Aftersales declares its settings in Platform's settings store
  (KAN-805) and reads them through `platform.public`, naming the Betrieb and the location; it owns no
  settings table (A-RULE-0). Lists and rules (labour rates, the service catalogue) are data of their
  area, not settings.
- **Planning (ADR-211).** Bookings, blocks and the overlap rule are `planning`'s (`planning.md`);
  Aftersales calls `planning.public` and keeps «neu einplanen» from planning's events.
- **States and refusals** are the slice page's section 3 tables (order, job and answer, position,
  stay, clock, check, «Rechnungsbereit», booking); a refusal says why in one sentence. Its 49 checks
  (section 7) are the acceptance tests: a ticket cites its checks by number.
- **Who may do what** (ruling 180): every decision at the desk is open to the role `aftersales`; the
  technician has what is on their phone (no prices anywhere, decision 7); the manager has everything.
  Every screen sits behind the `aftersales` module switch (off → 404 for everybody).
- A billing category exists on every job from the first day, with one value («customer»); later
  slices add values, not a field. A service-catalogue package is **copied** onto a job, never linked.
