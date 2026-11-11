---
paths:
  - "app/vehicle/api/catalogue*.py"
  - "app/vehicle/services/catalogue*.py"
  - "app/vehicle/services/energy_rating.py"
  - "app/vehicle/services/reference_codes.py"
  - "app/vehicle/services/provider.py"
  - "app/vehicle/services/provider_valuation_input.py"
  - "app/vehicle/models/catalogue*.py"
  - "app/vehicle/models/energy_rating.py"
  - "app/vehicle/models/provider.py"
  - "app/vehicle/daily_jobs.py"
  - "app/vehicle/schemas/catalogue.py"
  - "app/vehicle/schemas/spec_block.py"
  - "tests/test_catalogue_*.py"
  - "tests/catalogue_lookup_support.py"
  - "tests/test_vehicle_catalogue*.py"
  - "tests/test_vehicle_energy_rating.py"
  - "tests/test_vehicle_provider.py"
  - "tests/test_provider_code_map_seed.py"
  - "tests/test_seed_demo_provider_code_map.py"
  - "tests/test_kan194_legacy_mapping_gap_code_groups.py"
  - "tests/architecture/test_catalogue_browse_is_a_bounded_live_query.py"
  - "tests/architecture/test_no_bulk_catalogue_retrieval.py"
  - "scripts/seed_demo_provider_code_map.py"
  - "scripts/import_bfe_energy_ratings.py"
  - "frontend/apps/dms/src/**/*atalogue*"
  - "frontend/apps/dms/src/**/*atalogue*/**"
  - "frontend/apps/dms/src/components/MappingGapsQueue.tsx"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises ADR-013 as amended by ADR-075,
ADR-042, ADR-044, ADR-072, ADR-111, ADR-112, ADR-123 and the identAPI test agreement's single-lookup
rule (KAN-485, ADR-023 as amended). Cut from vehicle.md by KAN-614 (2026-11-10) to the invariants;
vehicle.md was verified against main@73baf3f on 2026-10-25 - this cut was not re-verified. -->

# The catalogue: master data, browse, details, codes

## What is shared and what is not (ADR-013, ADR-075)
- **Variant master data is global** (auto-i-dat's written permission): `Brand`, `ModelGroup`,
  `ModelVariant` (spec block and list price), `VariantPrice`, `TypeApproval`, the raw codes a
  variant was read from and the energy rating a lookup stored — no `tenant_id`, readable by every
  Betrieb with or without a contract. **Licensed details are tenant-partitioned**: options, option
  features and relations, colours, tyres, images (`TenantScopedMixin`). Widening the share, or
  adding another provider's master data, is a new decision — never a side effect.
- What a lookup stored is kept indefinitely and shared (Anto); the plate-lookup cache keeps its
  30-day TTL. What is never done is retrieving or storing the whole catalogue.

## Single lookups only, for every provider (identAPI test agreement; KAN-485)
- No full seed, no daily delta, no `FzKeyChanged`, **no daily job reaching a provider** — the demo
  provider (`auto_i_dat_mock`) too. A variant enters only through identification or a browsed page;
  every variant either returns is stored through `upsert_model_variant` / `store_seen_variants`.
  `test_no_bulk_catalogue_retrieval.py` refuses `page[number]` outside the adapter and any
  catalogue query inside a loop or comprehension (its allow-list is by name, with the bound pinned).
- A sync visit fills a variant's NULL fields and never overwrites a set one; resolving a mapping
  gap fills what the gap left NULL — on variants, provider configurations (never an advisor's value
  or an override, never a host's frozen snapshot), options, colours and tyres, per tenant.

## Browse is a live identAPI query, bounded (ADR-112)
- `services/catalogue_browse.py` asks the Betrieb's own connection for brands of a kind, a brand's
  model groups (`onlyInProduction` in `build` mode, on the model-group list only), **one provider
  page** of variants (the identAPI's 10; `nextCursor` = the next page) and facet values — **one
  request per field, only once a brand is chosen and the user opens the filters control**. Never a
  page or a field the user did not ask for (`test_catalogue_browse_is_a_bounded_live_query.py`); the
  grid asks for the next page only after the user moved in it and never refetches on focus.
- A query without a brand or model group, a key that is no name, or an unknown kind or filter value
  is the user's 422 **before the gateway** — no breaker charge, no call-log row.
- Filters are the identAPI's own: a canonical code is translated through `ProviderCodeMap` (an
  unmapped value is 422; a facet value the map does not know is a gap and not offered); numbers only
  on the fields the identAPI marks filterable (`kw`, `displacement`, `doors`, `seats`, `co2` — never
  `ps`, the list price or the production years); there is no «is not».
- **States, never errors** (Anto): no connection → `notConfigured`, a 403 or `ident_basis` not
  granted → `notInContract`, failure or timeout → `unavailable` — each a 200 with `state`.
- The demo provider needs the real code map copied (`scripts/seed_demo_provider_code_map.py`) or
  every code is a gap.

## Details load once per Betrieb when the car is opened (ADR-123)
- `services/catalogue_details.py`: the specification read asks the Betrieb's provider for every
  detail it has not stored — colours (with the variant), options, tyres, images, energy label — each
  its own `call_capability` block, savepoint **and commit**, each ending in a `VariantDetailState`:
  `present` (possibly empty, also the provider's coherent "nothing") · `not_in_contract` (403, or a
  probed option absent — then nothing is sent) · `unavailable` (asked again next open) · no row =
  never asked. A failed detail never discards the car or the others.
- An advisory lock on (Betrieb, variant), per detail on the request's own connection, serialises two
  opens of the same car; the load holds no other connection; a shared-row unique violation is never
  a 500 (the loser re-reads the winner's row).
- **No automatic refresh of any kind**: «Neu laden» (`POST …/details/reload`, dealer manager or
  staff, audited) is the one refresh. The spec query neither refetches on focus nor retries.
- The energy label is stored once for every Betrieb as that year's `ModelVariantEnergyRating`
  (`source = auto_i_dat`), filling an absence only, never over a held row (a BFE row included), and
  only when every gearbox row agrees on the class (ADR-042 as ruled). A later BFE import rewrites
  value and source.
- Options carry the manufacturer code beside auto-i's key; relations (`contains` / `excludes` /
  `price_in_combination_with`) are stored and shown, **never enforced** (ADR-072). The colour's
  name is shown as delivered; its type is the advisor's pick from the `colour_type` /
  `interior_colour_type` lists. A failure's class name never reaches the screen
  (`DetailStateNotice`).

## Codes (ADR-044)
- Provider codes never appear in application code: canonical taxonomy + `ProviderCodeMap`, keyed
  `(provider, vehicle_kind, code_group, provider_code)` — `vehicle_kind` the raw kind, `code_group`
  the semantic list code; never a numeric CodeGrpNr (`resolve_provider_code` refuses one). The
  adapters decode nothing: colour area, axle and tyre type arrive raw and resolve through the map.
- An unmapped code of a code-mapped field becomes a `mapping_gap` (FR-C-10), never a silent
  default. **A code the provider did not send is no value and no gap** (Anto): a colour without one
  is hidden, a tyre without an axle reads «Nicht zugeordnet» (to revisit once a real account shows
  whether it happens).
- A motorcycle's `drive` is a stroke count, not a drivetrain: the sync routes it to `engine_cycle`
  by the **resolved** `vehicle_kind`, never a raw code; while the kind is a gap it is recorded as
  `drivetrain`. The seeded map (`7c4e9a2b6d13`, asserted by `tests/test_provider_code_map_seed.py`)
  is the identAPI's live map. The spec block's reference fields are code-mapped too; `euronorm` has
  no seeded rows yet — a gap on every lookup until mapped. `category` is the **size class**
  (`vehicle_class`: micro … luxury, seeded for passenger cars by `8e2b6c4f1a97`; Anto, KAN-587); the
  type-approval class (M1, N1 …) is `homologation_class`, its own list and spec field, which no
  provider sends — entered by hand.
- Provider text (option and colour names) is stored and rendered **as delivered**, never translated;
  only our canonical reference data is translated.
- Typenschein ↔ variant is many-to-many (`type_approval_number` indexed, not unique; use
  `find_model_variants_by_type_approval`). Keep the provider's full image URL — no proxy.

## Catalogue administration
The mapping-gap queue is `/catalogue?tab=mapping-gaps`; brands are created and renamed on
`/catalogue?tab=brands`, a tab platform staff alone see (`require_access_role()`): the code is set
once, the name edited with If-Match. `valuation` is its own context (`valuation.md`).
