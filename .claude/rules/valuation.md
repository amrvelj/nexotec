---
paths:
  - "app/valuation/**"
  - "app/sales/services/trade_in.py"
  - "frontend/apps/dms/src/**/*aluation*"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises PRD-Vehicles (FR-V-09,
FR-V-17), ADR-029, ADR-048 as amended, ADR-066, ADR-070. Verified against main@568f416 on
2026-09-28 (every present-tense claim checked against the code). Fix this file in the same PR
as any change to what it states. -->

# Valuations

- **`valuation` is its own bounded context** — "a dated commercial opinion, not a vehicle
  fact", with its own audit and retention rules. Not under `vehicle` (vehicle identity is
  global, valuations are tenant-private) and not under `sales` (Sales owns the trade-in
  workflow, not the valuation record).
- **Single writer.** Sales and Stock hold a `valuationRef` and read the same record; there is
  never a second writer.
- **Tenant-private even within a group** (ADR-029): a sister dealership never sees another's
  valuations.
- **A vehicle carries a LIST of valuations** (ADR-048 as amended, ADR-066). The newest is
  current; older ones stay readable as superseded and are never edited or deleted. A manual
  figure is **marked manual everywhere it renders**.
- **A valuation is a standalone application**: creatable with no customer, no offer and no
  vehicle in the register. It carries a **validity period**.
- **Status `draft → valid → expired`** (plus `used` once a contract consumes it) is **derived
  on read** — never stored, never repaired by a nightly job.
- **ADR-070** (amending FR-V-17): a *configuration* never writes vehicle-mdm; it attaches to a
  valuation. **Today `create_valuation` still creates or gets the vehicle-mdm record when a VIN
  is given** (`services/valuation.py`), and the offer trade-in (`sales/services/trade_in.py`)
  creates the vehicle itself and attaches an existing valid valuation. The record-mode
  valuation path from the offer is C-F (KAN-10), not built. Whether valuations keep creating
  vehicles is Anto's call — read ADR-070 and ask; never change it in passing.
