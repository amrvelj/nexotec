---
paths:
  - "app/vehicle/**"
  - "app/integration/**"
  - "scripts/verify_auto_i_dat.py"
  - "frontend/apps/dms/src/**/*atalogue*"
  - "frontend/apps/dms/src/**/*atalogue*/**"
  - "frontend/apps/dms/src/**/*ehicle*"
  - "frontend/apps/dms/src/**/*ehicle*/**"
  - "frontend/apps/dms/src/**/*onfigurat*"
  - "frontend/apps/dms/src/**/*onfigurat*/**"
  - "frontend/apps/dms/src/components/MappingGapsQueue.tsx"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises PRD-Vehicles, PRD-Configurator
v1.4 (ADR-068…ADR-072, host/mode matrix), Integrations & API Credentials, ADR-013, ADR-041,
ADR-044, ADR-045, KAN-36/38/43. Verified against main@568f416 on 2026-09-28 (every
present-tense claim checked against the code). "Not built" lines cite a ticket; /drift-audit
re-checks them weekly. -->

# Vehicles, catalogue, configurator and the provider gateway

## Identity

- **VIN is mandatory in vehicle-mdm.** A pre-VIN vehicle is a pipeline stock item in
  `inventory` (ADR-045), promoted on VIN arrival, idempotently by `pipeline_vehicle_id`
  (FR-V-04).
- **A licence plate is never an identifier** — Wechselschild (one plate, two vehicles),
  reassignment, cantonal changes. `vehicle_plate` is a child table with validity dates and a
  `plate_group_id`; an ambiguous lookup shows a picker and never guesses. Plate lookup must
  not be enumerable (architecture test).
- Vehicle identity is global; everything licensed about it is not (next section).

## Licensed data and the gateway

- **Licensed provider data is tenant-partitioned, never global** (ADR-013): auto-i-dat
  contracts are per dealer, so each dealer's cache is fetched with that dealer's credentials
  and never travels through the cross-tenant shared identity response (FR-V-14; architecture
  test on its shape).
- Every auto-i-dat call goes through the gateway (architecture test). Provider codes never
  appear in application code: canonical taxonomy + `provider_code_map`.
- **One registry, many gateways.** `integration` owns connections, write-only secret refs
  (`integration_secret_ref`, one row per secret slot; values live in the secrets manager,
  never in the app database), entitlements, call log and retention. The auto-i-dat adapters
  live in `integration` beside the registry. The **catalogue mirror** (variants, options,
  colours, tyres) is `vehicle`'s job, calling `integration.public.call_capability`.
- Catalogue browse makes **no** live provider call (architecture test).
- Provider text (option names, colour names) is stored and rendered **as delivered**, never
  translated (ADR-044); only our canonical reference data is translated.
- Everything provider-backed has run against the **mock only**: no staging auto-i-dat account
  exists yet. Say so in any claim about real provider behaviour.

## Configurator (ADR-068 … ADR-072)

- **ADR-068** — a configuration is a first-class entity with its own ID, referenced by an
  offer, a stock item, a valuation or a vehicle. No list screen, no nav entry.
- **ADR-069** — the configurator is provider-backed (reverses PRD-Sales v2 S-D05). Manual
  configuration stays as the off-catalogue path: grey imports, oldtimers, pre-1982 vehicles,
  anything auto-i-dat does not cover.
- **ADR-070** — a configuration **never writes vehicle-mdm** (architecture test). It attaches
  to a pipeline stock item, a valuation, or an existing vehicle when a VIN is known.
- **ADR-071** — one specification block, three carriers: the catalogue variant, the
  configuration, and the host's frozen snapshot. A field on one and not the others is a defect
  (`test_spec_block_carriers_do_not_drift.py`). `ModelVariant` and `VehicleConfiguration`
  carry it; the offer's frozen `vehicle_snapshot` (ADR-041,
  `sales/services/snapshot.py::freeze_vehicle_snapshot`) carries it under `spec` when Path B
  attached a configuration (KAN-10).
- **ADR-072** — option packages, exclusions and extra conditions are stored and shown, never
  enforced: a conflict warns, it never blocks.
- **Host / mode matrix** (PRD v1.4): Stock → add to pipeline: both (`build` factory order,
  `record` bought in) · Offer → new configuration: `build` only · Offer → trade-in: `record`,
  via the valuation path · Valuation → new valuation: `record` only. The configuration entity
  supports both modes; the host decides which is reachable. Never bake a mode switch into the
  offer overlay. Each host enforces it at its API (`configuration_mode_not_allowed`, 422) and
  the overlay takes `allowedModes`; hosts read a configuration only through
  `vehicle.public.get_configuration_for_host` (KAN-10).
- **Identification (FR-C-02, KAN-42)** is `services/identification.py`, one input
  (`GET /v1/vehicle-identification`): VIN → vehicle-mdm, then the entitled provider decode
  (not called: no specification, KAN-81 — calling the stub would trip the connection's
  circuit breaker); plate → `KontrollschildInfo` behind the per-tenant plate-lookup cache
  (30-day TTL, daily purge, never enumerable); Stammnummer → vehicle-mdm, then that cache;
  Typenschein and Werkscode → the mirror. Several hits are a picker, never a choice; a
  `FahrzeugeMatch` best match is a proposal, confirmed through `confirmedBestMatchCode`.
- **Re-sync (FR-C-16)** is only ever the advisor's request (`/v1/configurations/{id}/resync`):
  disagreeing fields listed, overrides marked, only chosen fields applied.

## auto-i-dat facts that are easy to get wrong

- **VIN decode is DAT-backed.** It needs a second, independent DAT sub-account, modelled as its
  own `dat` provider/connection — never folded into the `auto_i_dat` connection (they rotate
  independently). `vin_decode` is a **derived** entitlement
  (`app/integration/services/connections.py::compute_vin_decode_entitlement`), never
  hand-declared. The webservice call itself is **not implemented**:
  `AutoIDatSoapAdapter.decode_vin` raises `NotImplementedError` until auto-i-dat supplies the
  specification — a procurement step, not an engineering one (KAN-81).
- **Typenschein** — `vehicle_type_approval` is many-to-many with `vehicle_model_variant`;
  `type_approval_number` is indexed, not unique. Use `find_model_variants_by_type_approval`
  for the reverse lookup.
- **`Antrieb` CodeGrpNr 112 is 2-Takt / 4-Takt / Kein Takt** — a stroke count, not a
  drivetrain (groups 012 and 022 are Hinten/Vorne/Allrad). The seeded code map (migration
  `7c4e9a2b6d13`, asserted in `tests/test_provider_code_map_seed.py`) maps 112 →
  `engine_cycle`. **The sync does not read that row yet:** `catalogue_sync.py` resolves
  `Antrieb` under `drivetrain` for every vehicle kind and never writes `engine_cycle`. Routing
  by vehicle kind waits for a staging account — whether the provider selects the code group by
  `FzArt` or `FzArtExtern` is unknown (`scripts/verify_auto_i_dat.py` prints it); never route
  it on a guess.
- **`ProviderCodeMap` is keyed `(provider, vehicle_kind, code_group, provider_code)`** —
  `vehicle_kind` is the raw FzArt, `code_group` the semantic list code, `provider_code` the raw
  code; never a numeric CodeGrpNr. Unmapped codes become a `mapping_gap` (FR-C-10), never a
  silent default.
- **Keep the provider's full image URL** (`ImageRef.image_url`); the provider returns a
  directly fetchable URL per image — no proxy, no base-URL reconstruction.
- Stammnummer resolves only against our own data (vehicle-mdm, then the plate-lookup cache);
  auto-i-dat accepts no Stammnummer search. The catalogue sync links each variant to its
  Typenscheine (`vehicle_variant_type_approval`, KAN-42).

## Catalogue administration

The mapping-gap queue has a screen (`components/MappingGapsQueue.tsx`, on
`/catalogue?tab=mapping-gaps`); reference data has `/settings/reference`; **brand create and
edit have no screen** (the API exists).

`valuation` is its own context — see `.claude/rules/valuation.md`, not this file.
