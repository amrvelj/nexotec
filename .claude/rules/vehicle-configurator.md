---
paths:
  - "app/vehicle/api/configuration.py"
  - "app/vehicle/api/identification.py"
  - "app/vehicle/services/configuration*.py"
  - "app/vehicle/services/host_configuration.py"
  - "app/vehicle/services/identification*.py"
  - "app/vehicle/services/matching.py"
  - "app/vehicle/services/plate_lookup_cache.py"
  - "app/vehicle/models/configuration.py"
  - "app/vehicle/models/spec_block.py"
  - "app/vehicle/models/plate_lookup_cache.py"
  - "app/sales/services/snapshot.py"
  - "app/vehicle/schemas/configuration.py"
  - "app/vehicle/schemas/identification.py"
  - "app/vehicle/schemas/spec_block.py"
  - "tests/test_configuration*.py"
  - "tests/configuration_test_support.py"
  - "tests/test_configurator_*.py"
  - "tests/test_vehicle_identification*.py"
  - "tests/test_vehicle_configuration_retention.py"
  - "tests/test_vehicle_matching.py"
  - "tests/test_kan240_configuration_heal.py"
  - "tests/architecture/test_spec_block_carriers_do_not_drift.py"
  - "tests/architecture/test_configuration_never_writes_vehicle_mdm.py"
  - "frontend/apps/dms/src/components/configurator/**"
  - "frontend/apps/dms/src/configurationOptions*"
  - "frontend/apps/dms/src/pages/configurationLabelColumns*"
  - "frontend/apps/dms/src/pages/ConfiguratorHosts*"
  - "frontend/apps/dms/src/test/configuratorFixtures.ts"
  - "frontend/apps/dms/src/i18n/locales/*/configurator.json"
  - "frontend/apps/dms/src/components/stock-detail/*onfigurat*"
  - "frontend/apps/dms/src/pages/StockDetailPage.attachConfiguration*"
  - "frontend/apps/dms/src/pages/stock/AddToPipelineButton.tsx"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises PRD-Configurator v1.4 (ADR-068…ADR-072,
the host/mode matrix), ADR-041, ADR-083, ADR-114, ADR-121. Cut from vehicle.md by KAN-614 (2026-11-10)
to the invariants; vehicle.md was verified against main@73baf3f on 2026-10-25 - this cut was not
re-verified against the code. -->

# The configurator (PRD-Configurator v1.4; ADR-068 … ADR-072)

## The entity
- **ADR-068** — a configuration is a first-class entity with its own id, referenced by an offer, a
  stock item, a valuation or a vehicle. No list screen, no nav entry.
- **ADR-069** — provider-backed; manual configuration stays as the off-catalogue path (grey
  imports, oldtimers, anything auto-i-dat does not cover).
- **ADR-070** — a configuration **never writes vehicle-mdm** (`test_configuration_never_writes_vehicle_mdm.py`);
  it attaches to a pipeline stock item, a valuation, or an existing vehicle when a VIN is known.
- **ADR-071** — one specification block, three carriers: the catalogue variant, the configuration,
  the host's frozen snapshot (`sales/services/snapshot.py::freeze_vehicle_snapshot`, under `spec`).
  A field on one and not the others is a defect (`test_spec_block_carriers_do_not_drift.py`).
- **ADR-072** — packages, exclusions and extra conditions are stored and shown, never enforced: a
  conflict warns, never blocks.
- The configurator is a Sales-module surface: every route of `api/configuration.py` and
  `api/identification.py` is behind the router-level `sales` entitlement (404 without it;
  `test_sales_routers_carry_entitlement_gate.py`).

## Retention (Anto)
A configuration no host ever took is deleted 30 days after creation; one a host took is kept for
good. `first_taken_at` is stamped (never moved or cleared) by vehicle's consumers of the hosts' own
facts (`sales.offer|contract.configuration_attached`, `inventory.stock_item.added` /
`.configuration_attached`, `valuation.created`). The daily `vehicle.configurations.purge_unattached`
also asks Sales, Stock and Valuation through their read-only `referenced_configuration_ids` — handed in
by `app.worker.CONFIGURATION_HOSTS`, never imported by vehicle (ADR-131, KAN-633) — counts
one linked to a registered car as held, stamps every held one, and deletes the rest past 30 days —
each in its own transaction, with an audit row and `vehicle.configuration.discarded` — then blanks
the personal values earlier audit rows copied through `app.core.audit.redact_audit_values`, the
log's one sanctioned rewrite (ADR-121); discarding a host's failed copy does the same. A copy
(FR-C-11) is a new configuration, unstamped until taken. Residual race (the service's docstring): one
past 30 days that a host attaches while the job runs is deleted under it, and the host's
reconciliation reports the dangling reference.

## Hosts and modes
Which host offers which mode, and how a host reads and copies a configuration: `vehicle-configurator-hosts.md`.

## Identification (FR-C-02; `services/identification*.py`, `GET /v1/vehicle-identification`)
- One input, rungs by kind: **VIN** → vehicle-mdm, then `identify_by_vin` only where the
  connection's probed `vin` row is granted (no row is not granted; not in the test licence) — else
  the note `vin_decode_not_entitled` and no call. **Kontrollschild** → the provider first (ADR-114,
  never vehicle-mdm's plate register) behind the per-tenant plate-lookup cache (30-day TTL, daily
  purge, never enumerable); one registered car is one candidate, several the picker. **Stammnummer**
  → vehicle-mdm, `identify_by_vrn`, then the cache. **Typenschein / Werkscode** → one provider page
  merged with the stored variants.
- The cache is filled by a plate lookup only, **whole or not at all** — a part would later read as
  the whole truth about the plate; a `vrn` answer never fills it. Each row keeps the stored variant
  the answer named (`model_variant_id`, KAN-592): a hit on one such record is that one candidate, as
  live, and the picker's records carry it as their one candidate, so picking one offers that variant
  to confirm without a `vehiclecatalog` call. A row without one (older, or a variant no longer stored) answers from the
  stored variants at its Typenschein and calls only when none are stored.
- Every variant a rung returns is stored (master data, each upsert in a savepoint, a lost race
  re-read in a fresh savepoint up to four attempts — KAN-593); the stored rows are a cache of
  what was seen, never the source. `vehicle.plate_lookup.conflicted` names only records carrying
  a Stammnummer, once per provider answer (no per-day dedupe, KAN-593).
- Refusals and failures are **notes, never errors** (`provider_not_entitled`, `provider_unavailable`,
  `plate_lookup_*`; `configurator.identify.notes.*`); the rung falls through to what we hold.
  **Several hits are a picker, never a choice**; the best match (`find_best_match`) is a proposal,
  confirmed through `confirmedBestMatchCode`. A Wechselschild is informational blue (ADR-114).
- A configuration's read carries `energyRating`: the variant's rating for the first-registration
  year, else the model year, else the newest held.

## Fields
- **The five coded fields** (`vehicleKind`, `fuelType`, `bodyStyle`, `drivetrain`, `transmission`)
  are never empty by an advisor's hand (Anto): locked on a provider configuration (422
  `coded_field_locked`; the same value is accepted); a manual configuration needs all five (422
  `coded_fields_required`) and cannot clear one. They and the spec-block reference fields take only
  an active value of their reference list.
- Every other nullable field clears with an explicit `null` on PATCH; an absent field is left alone.
  The configure phase's VIN field, emptied, saves `vin: null`, which drops the vehicle link.
  `catalogueVariantId`, `catalogueMatchStatus` and `spec: null` still mean "leave alone".
- **Re-sync (FR-C-16)** is only ever the advisor's request: disagreeing fields listed, overrides
  marked, only chosen fields applied.
- Audit rows name a field by its camelCase API name; older rows keep their snake_case keys (the log
  is append-only).
