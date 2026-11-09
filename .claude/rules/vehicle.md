---
paths:
  - "app/vehicle/**"
  - "app/integration/**"
  - "scripts/seed_demo_provider_code_map.py"
  - "frontend/apps/dms/src/**/*atalogue*"
  - "frontend/apps/dms/src/**/*atalogue*/**"
  - "frontend/apps/dms/src/**/*ehicle*"
  - "frontend/apps/dms/src/**/*ehicle*/**"
  - "frontend/apps/dms/src/**/*onfigurat*"
  - "frontend/apps/dms/src/**/*onfigurat*/**"
  - "frontend/apps/dms/src/components/MappingGapsQueue.tsx"
  - "frontend/apps/dms/src/**/*ntegration*"
  - "frontend/apps/dms/src/components/connectionRowMenu.tsx"
  - "alembic/versions/vehicle/**"
  - "alembic/versions/integration/**"
  - "app/sales/services/snapshot.py"
  - "tests/architecture/test_plate_lookup_is_not_enumerable.py"
  - "tests/architecture/test_spec_block_carriers_do_not_drift.py"
  - "tests/architecture/test_auto_i_dat_calls_go_through_the_gateway.py"
  - "tests/architecture/test_catalogue_browse_is_a_bounded_live_query.py"
  - "tests/architecture/test_no_bulk_catalogue_retrieval.py"
  - "tests/architecture/test_configuration_never_writes_vehicle_mdm.py"
  - "tests/architecture/test_shared_identity_lookup_response_shape.py"
  - "tests/test_provider_code_map_seed.py"
  - "frontend/apps/dms/src/pages/stock/AddToPipelineButton.tsx"
  - "app/core/validators.py"
  - "frontend/apps/dms/src/utils/vin.ts"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises PRD-Vehicles, PRD-Configurator
v1.4 (ADR-068…ADR-072, host/mode matrix), Integrations & API Credentials, ADR-013 as amended
by ADR-075, ADR-041, ADR-044, ADR-045, ADR-083, ADR-085, ADR-090, KAN-36/38/43; also cites
ADR-043. Verified against main@73baf3f on 2026-10-25 (every present-tense claim checked against
the code). "Not built" lines cite a ticket; /drift-audit re-checks them weekly. -->

# Vehicles, catalogue, configurator and the provider gateway

## Identity

- **VIN is mandatory in vehicle-mdm.** A pre-VIN vehicle is a pipeline stock item in
  `inventory` (ADR-045), to be promoted on VIN arrival by
  `inventory/services/pipeline.py::promote_and_flush`, idempotent per stock item (FR-V-04).
  Its one production caller is purchase booking (ADR-103, KAN-446): a pipeline item is bought
  with its VIN, and the booking promotes it in the same transaction.
- **A typed VIN is one spelling in every write** (FR-V-15): every request-body field carrying a
  VIN a person typed is normalised (upper-cased, whitespace removed): stock create and the
  trade-in through `app.core.validators.NormalisedVin` (then ISO 3779; 422 otherwise; KAN-244,
  KAN-331), the valuation create and the configuration create/PATCH through `NormalisedVinOrChassisNumber`
  (a VIN, or a pre-1981 chassis number — next bullet; KAN-338, KAN-353); the configurator mirrors it (`utils/vin.ts`). vehicle-mdm's
  own fields keep `Vin`, which refuses an unnormalised value rather than fixing it. The shared
  identity lookup (`GET /v1/vehicle-mdm/lookup?vin=`) normalises its query parameter with the
  same `normalise_vin` and answers 404 for a value that is no VIN — a read, never 422 (KAN-355).
  The list's one search box (`services/search.py::resolve_identifier`) normalises the typed `q`
  the same way before testing the VIN shape, so a lower-cased or spaced VIN is a direct hit
  (KAN-360). A refused VIN is named on screen in the user's language (`common.invalidVin`,
  `utils/vin.ts::isVinRefusal`, KAN-351).
- **A pre-1981 chassis number** (KAN-353, Anto 2026-10-18, option C): the valuation's and the
  configuration's `vin` are `NormalisedVinOrChassisNumber` — a VIN, or 1-17 letters and digits
  (I, O, Q allowed) — and `require_vin_or_pre_1981_chassis_number` accepts the latter only with
  a first registration before 1981-01-01 (on a configuration PATCH, the stored VIN and date count
  for whichever the PATCH leaves out — how a VIN stored before KAN-338 is judged, below);
  otherwise 422 `chassis_number_requires_pre_1981_registration` (`common.chassisNumberNeedsPre1981`).
  Such a number is stored and printed normalised, as a VIN is (upper case, no spaces; punctuation
  refused): one spelling per car, never "as entered" (KAN-391, Anto, option A: "chassis
  number and VIN is the same") — and **never** finds, creates or links a
  vehicle-mdm record (`is_chassis_number`) — a valuation is judged on the VIN and date it stores
  (its own, else its configuration's) and links no car when that VIN is a chassis number. **A VIN
  stored before KAN-338** is judged on every configuration PATCH like a typed one, so one that is no
  VIN blocked every save (KAN-395; Anto, 2026-10-20, option B — clean the data once):
  `scripts/repair_configuration_vins.py` (dry run unless `--commit`; `configuration.repair_stored_vins`)
  normalises what normalises to a VIN, clears the rest, leaves a car first registered before 1981
  alone (one stored before KAN-338 in another spelling is KAN-502's question), and audits each change (`vin_repair`, the old value kept); run it
  once per environment. A valuation's stored VIN is never edited (valuation.md). Stock and the trade-in keep `NormalisedVin`. The
  configurator's configure phase has a VIN field with a clear control and shows the linked
  vehicle's number and VIN (as its own detail header names it; read from `GET /vehicle-mdm/{id}`)
  or «Kein Fahrzeug verknüpft» (KAN-340): a VIN, or such a chassis number, is
  typed or corrected there; an emptied field saves `vin: null`, which drops the link (KAN-326).
- **A licence plate is never an identifier** — Wechselschild (one plate, two vehicles),
  reassignment, cantonal changes. `vehicle_plate` is a child table with validity dates and a
  `plate_group_id`; an ambiguous lookup shows a picker and never guesses. Plate lookup must
  not be enumerable (architecture test).
- **The Kontrollschild register is the dealer group's** (ADR-039 as amended; Anto, 2026-10-31,
  KAN-467): `vehicle_plate.group_id`, and every read and write in `services/plate.py` names the
  caller's group from the token — search, the Plates tab, the shared lookup's `currentPlate` and
  the trade-in's plate match (`vehicle.public.match_vehicle` takes `group_id`, required) never see
  another group's plate, which may sit on another car (`plate.py` is on
  `test_no_ambient_group_read.py`'s allow-list for it). Nightly reconciliation checks
  `vehicle_plate.group_id -> dealer_group.id` and that it is the recording Betrieb's group. The
  cross-group conflict queue (`vehicle_plate_conflict`) is gone. A plate is stored in one spelling
  (`normalise_kontrollschild`: canton code + «ZH 123456»; 422 `plate_canton_invalid` /
  `plate_invalid`); «current» is the **Swiss** day (`plate.swiss_today`, `session_timezone`), never
  the UTC one. **Recording** («Kontrollschild erfassen» on the Plates tab and in the row menu):
  `POST /v1/vehicle-mdm/{id}/plates`; a plate current on another car of the group is 409
  `plate_current_on_other_vehicle` with that car in `details.holders` (with each assignment's
  `version`). **Naming that car is a plate read**: the route passes it through
  `plate_read_guard.may_name_plate_holders` (audited, counted against the per-user limit, purpose
  `plate_recording_refused`); past the limit the refusal carries `holdersWithheld` instead, and a
  Wechselschild partner the refusal did not name gets a refusal that names nobody. The user's answer
  is the same POST with `moveFrom` (exactly the listed assignments, each with its version — their
  If-Match; each ends the day before) or `wechselschildWith` (the one other car; a group of at most
  two), serialised per plate by an advisory lock; «beenden» is `POST …/plates/{plate_id}/end` under
  If-Match on the versioned row, taking the same lock — rows are never deleted. **A sold car's plate**
  (Anto's second way in, 2026-10-26) is **built (KAN-523)**: entered at Sales' «Rechnung anfordern»,
  checked before the request by `vehicle.public.check_plate_handover` (the register's own refusals,
  `plate.check_plate_recording`, read-only; holders named through the plate-read guard, purpose
  `invoice_plate_refused`), and recorded by the consumer `vehicle.plate_on_invoice_requested`
  (`app/vehicle/consumers.py`, `services/plate_handover.py`) through `record_plate`, valid from the
  contract's delivery date or the day invoicing was requested, with the user's confirmed `moveFrom`; a
  plate the car already carries is a no-op, any other refusal (a plate moved meanwhile) is raised —
  retried, then dead-lettered. The third, the incumbent-DMS import (FR-V-08 — a WP-10 requirement), is
  **not built**. Audit rows of a plate write
  (`plate_recorded`, `plate_ended`, `plate_wechselschild_formed`) carry validity and plate group,
  never the plate value. Every write service in `app/vehicle/services` is reached from production
  code (`tests/architecture/test_vehicle_write_services_have_a_caller.py`, KAN-467; exceptions
  KAN-524).
- **Plate-history reads (Plates tab, resolved search hit, shared identity lookup) are audited and limited per user**
  (ADR-085, KAN-231): only through `services/plate_read_guard.py::read_plate_history`, never
  `plate.list_plates_for_vehicle` directly (architecture test); every read and every refusal
  writes an `audit_event`, never the plate values; it reads the caller's group's register only. Past `plate_read_limit` distinct vehicles per
  `plate_read_window_seconds` (`app/core/config.py`, platform-wide by design — never a
  dealership setting; default 60 per hour) the Plates tab gets 403 `plate_read_limit_reached`
  and a search hit `currentPlateWithheld`; the car page still opens. `GET /v1/vehicle-mdm/lookup`
  (ADR-043) reads its `currentPlate` through the same guard (KAN-279) and, past the limit,
  answers `currentPlate: null` — ADR-043's shape has no withheld flag, so withheld and no
  plate look the same there. `tests/architecture/test_plate_lookup_is_not_enumerable.py`
  refuses any read of `VehiclePlate` or `VehiclePlateConflict` outside `plate.py` /
  `plate_read_guard.py`, except the uses it names that hand out no plate (the model registry,
  the FR-V-12 merge, reconciliation).
- **A changed label is an event** (KAN-186, Anto option A, ADR-119): `services/vehicle_mdm.py::update_vehicle_mdm`
  publishes `vehicle.vehicle_mdm.summary_changed` in its own transaction whenever a field
  `vehicle.public.VehicleSummary` carries changes (VIN, vehicle number, make/model/trim from the
  catalogue match, model year from the first registration) — and only then: a Stammnummer, a
  status or a first registration in the same year publishes nothing, a refused write nothing.
  `tenant_id` is null (vehicle-mdm is global); the payload is `vehicleId`, `version`,
  `changedFields` and the whole `summary`. Any new writer of a summary field calls
  `publish_summary_changed_if_changed` with the summary read before its write. **Ruled
  exception:** a brand rename (`catalogue_admin.update_brand`, platform staff, rare) changes the
  make of every car of the brand and publishes nothing — the customer's Vehicles tab shows the new
  make the next morning, by the nightly `customer.vehicle_party_labels` refresh (Anto, 2026-11-05,
  KAN-560 option B: no rename event, no per-car flood; pinned by
  `test_a_brand_rename_reaches_the_vehicles_tab_the_next_morning`). No production
  code writes a vehicle-mdm record's `catalogue_variant_id` after creation yet. The FR-V-12 merge changes no summary field
  of either record; its party re-point relabels through `customer.public.repoint_vehicle_party`.
- **Every identity PATCH is audited** (KAN-561): `update_vehicle_mdm` writes an `update` audit row (the
  actor, the changed fields before and after under their camelCase names; none for a PATCH that changes
  nothing; `tenant_id` null — vehicle-mdm is global). `"vin": null` or `"vehicleStatus": null` is a 422;
  a duplicate VIN or Stammnummer is a 409 that names that field (`details.vin` / `details.stammnummer`),
  and any other integrity error is not reported as a duplicate.
- **Any Betrieb may end a recorded accessory; every end is audited** (Anto, 2026-11-03, KAN-427
  option B: the car is shared). `DELETE /v1/vehicle-mdm/{id}/accessories/{id}` sets `validTo` to
  today in Switzerland (`plate.swiss_today`, never the UTC day) and writes an `accessory_ended` audit
  row (actor, the row before and after — its `recordingTenantId` included) under the ending Betrieb and,
  when another Betrieb recorded it, once more under that one (Anto, 2026-11-04, KAN-427 option A: both
  see it); the row is never deleted (FR-V-13), and an accessory already ended is left as it is.
- Vehicle identity is global; everything licensed about it is not (next section).

## Licensed data and the gateway

- **Licensed provider data is tenant-partitioned** (ADR-013, amended by ADR-075): options,
  option features and relations, colours, tyres and images are fetched with each
  dealer's own credentials and quota (`TenantScopedMixin`) and never travel through the
  cross-tenant shared identity response (FR-V-14; architecture test on its shape).
- **ADR-075 — auto-i-dat variant master data is global**, by auto-i-dat's written permission:
  `Brand`, `ModelGroup`, `ModelVariant` (spec block and list price included), `VariantPrice`
  and `TypeApproval` — and the raw codes a variant was read from (`VariantProviderCode`,
  KAN-83) and the energy rating a lookup stored for the year (`ModelVariantEnergyRating`,
  `source = auto_i_dat`, KAN-547) — carry no `tenant_id` and are readable by every dealer, with or without an
  auto-i-dat contract, including through a VIN or Stammnummer lookup. Widening the share to
  options, colours, tyres or images, or adding another provider's master data, is a new
  decision — never a side effect.
- A sync visit fills a variant's NULL fields and never overwrites a set one; resolving a
  mapping gap fills the variants it left NULL at once (KAN-83), the NULL spec and coded fields
  of the provider configurations built from them — never an advisor's value or an overridden
  field, never a host's frozen snapshot (Anto, 2026-10-12, KAN-240) — and adds an
  `equipment_feature` to every option whose raw `SuchCode` list carried it, per tenant
  (`VariantOptionProviderCode`, KAN-250), and fills every cached colour's `colour_type` and
  tyre's `axle`/`season` still NULL whose raw code it was, per tenant (the raw code kept on the
  row, KAN-75, Anto's B1).
- Every auto-i-dat call goes through the gateway (architecture test). Provider codes never
  appear in application code: canonical taxonomy + `ProviderCodeMap` (`vehicle_provider_code_map`).
  The adapters decode nothing: `FarbArt`, `AchsenCode` and `PneuTyp` arrive raw too and resolve
  into `colour_area` (exterior / interior), `axle_position` (front / rear / both) and `tyre_type`
  (KAN-75, Anto 2026-10-12 option A — `7c4e9a2b6d13`'s "a map row would be a second definition"
  is reversed on purpose; seed `b0a125a0a583`, for `auto_i_dat` and the mock alike, C1). A colour
  whose code is a gap has no `colour_type` and the configurator offers it in neither group (A2);
  a tyre row is keyed by its raw `axle_code` + `season_code`.
- **One registry, many gateways.** `integration` owns connections, write-only secret refs
  (`integration_secret_ref`, one row per secret slot; values live in the secrets manager,
  never in the app database), entitlements, call log and retention. The auto-i-dat adapters
  live in `integration` beside the registry. The **catalogue mirror** (variants, options,
  colours, tyres) is `vehicle`'s job, calling `integration.public.call_capability`.
- **auto-i-dat is the identAPI** (ADR-111; KAN-488): `auto_i_dat` resolves to
  `adapters/auto_i_dat_ident.py`, a REST / json:api transport over `httpx` on
  `https://api.ident.autoi.ch`, with one choke point `_request` (one call-log row per attempt
  under the operation's own name — `caption_code`, `vehiclecatalog`, …; a timeout or 5xx retried
  once, a 4xx never; 401 → the token is dropped and requested once more, 403 →
  `ProviderNotEntitledError`, 400/422 → `ProviderRequestError`). **Login** is
  `adapters/autoi_oauth.py`: metadata discovery from `https://www.autoi.ch/.well-known/oauth-authorization-server`
  (cached ≤ 24 h, never hard-coded) and auto-i's **User Enrollment Grant** (their instruction of
  2026-11-07; ADR-111 as amended): `grant_type=user_enrollment`, `scope=ident`, HTTP Basic with the
  service account's client id (`config.clientId`) and client secret (slot `client_secret`), naming
  the **acting Nexotec user** (`user_id`, `email`, `first_name`, `last_name`, read through
  `platform.public.get_active_user_acting_in`) — auto-i registers each user on first use. The
  Bearer token (1200 s) is cached in process **per connection and per user**, keyed by the
  connection's `version` and `rotated_at` (an edit or a rotation drops it), never persisted,
  never logged (`access_token` / `Authorization` are redacted field names); the discovery read and
  each token request are rows of their own (`oauth_discovery`, `oauth_token`, both `cost_units = 0`),
  the secret resolved and audited once per token request and only after discovery produced an
  endpoint. A call with no signed-in user, no `clientId` or no secret is a
  `ProviderConfigurationError` — the dealer's to fix: the breaker is never charged and no row is
  invented for a request that was not sent (what was sent before it stopped, the discovery read,
  keeps its row). A 400/422 and a 403 are the provider's coherent answers (our request, our
  licence), logged `ERROR` on their row and never charged to the breaker — one un-entitled option
  must not switch off the others; a transport failure, a 5xx and a refused login charge it. The provider row (migration `6b98739fe743`) is
  `auth_type oauth2_user_enrollment`, slots `[client_secret]`, config `[clientId]`,
  `capability_codes [ident_basis, ausstattung, bewertung, vin, pneu, energielabel]`: the identAPI's
  licence options, each probed by one documented call on «Test connection» (inputs are auto-i's
  own documentation examples; 403 = not granted, 200/404 = granted, anything else leaves the row).
  `images`/`packages` are features over `ausstattung`; the plate rung's entitlement is
  `ident_basis`. «Test connection» = discovery (cached a day) + token + `GET /caption/code?lang=de`
  + the captions in fr/it/en (KAN-547) + the six probes, with the clicking user as the actor; a login refused under the probes charges
  the breaker once, stops them, and leaves the remaining options' rows as they were. **Built on the
  identAPI adapter (KAN-547 PR 1):** `identify_by_vrn` / `identify_by_plate` (`GET /vehiclebuild/{vrn|licenseplate}/…?needAutoiKeyIfExists`,
  one `vehiclebuild` row; a Wechselschild is several `BuiltVehicleData`), `find_variants_by_type_approval` /
  `find_variants_by_werkscode` / `fetch_variant` (`GET /vehiclecatalog/{typeapproval|factorycode|autoi}/…`,
  the first provider page of 10, never a second — `vehiclecatalog`), `find_best_match`
  (`GET /vehiclematch/typeapproval/{nr}/{newPriceCatalog}` — `vehiclematch`; `matchCode` 1 exact, 2 best of
  several, anything else "no match"), `fetch_captions`, and KAN-548's browse operations
  (`list_catalogue_brands`, `list_catalogue_model_groups`, `search_catalogue_variants` — one page, `includeCount`,
  refused in code without a brand or model-group scope —, `facet_values` = `fields=<field> distinct`). Bodies are
  read by `adapters/identapi_parse.py`, which interprets no code: `0` is *unknown* (NULL), a null `productionEnd`
  is *in production*, a Stammnummer loses its dots and a plate its spaces. **The details (KAN-211, ADR-123):**
  `fetch_variant_with_colours` (`GET /vehiclecatalog/autoi/{fzKey}` — the variant row carries `colorOptionsData`;
  the identAPI has no colour endpoint), `fetch_options` (`GET /vehicleoption/catalog/autoi/{fzKey}/modelyear/{y}`,
  `0` = currently available; the one bounded walk over `page[number]`: it follows `links.next` for the one car
  opened, at most `OPTIONS_PAGE_CAP` = 30 pages, each page its own `vehicleoption_catalog` row, pinned by
  `test_the_options_walk_is_bounded_by_the_page_cap`), `fetch_tyre_specs` (`GET /tyresandrims/typeapproval/{nr}`,
  one request, both seasons — each row names its `tyreType`), `fetch_images` (`GET /image/vehicle/{fzKey}`, one
  request, every size and perspective, stored under the S/L and A/I codes `ImageRef` has always held) and
  `fetch_energy_label` (`GET /energylabel/info/typeapproval/{nr}`, one row per gearbox with its
  `calculationYear`). **The VIN rung (KAN-549):** `identify_by_vin` (`GET /vehiclebuild/vin/{vin}?needAutoiKeyIfExists`,
  one `vehicle_vin` row — named apart from `vehiclebuild` so the call log tells the licence option `vin` from a
  plate or Stammnummer lookup; the `vin` probe calls the same endpoint, Anto 2026-11-09 option A). **Valuation
  (KAN-552):** `fetch_vehicle_valuation` (`GET /vehiclevaluation/{fzKey}/{modelYear}/{firstAdmission}/{km}`, options as
  `option[n].key`, messages in DE/FR/IT/EN — one `vehiclevaluation` row) and `fetch_residual_table`
  (`GET /residualvalue/{fzKey}/{modelYear}?firstAdmission&kilometreState`, the predefined table in one
  `residualvalue_table` row; the single-cell residual call is not built — no caller); Valuation's caller and its
  `vehicle.public.get_provider_valuation_input` are in `valuation.md`. **`ProviderAdapter` is exactly these identAPI
  operations** (KAN-550; `tests/architecture/test_provider_adapter_is_the_identapi.py`): the identAPI adapter implements
  them plus its access check and six probes; the demo provider (`auto_i_dat_mock`) implements exactly them over invented
  fixtures carrying auto-i's own codes (kind `01`, gearbox `1`/`5`, …) and meters each request under the identAPI
  adapter's operation name, so demos and tests run on it. *Historical:* the SOAP «Webservice Fahrzeuge» adapter
  (zeep transport, AES decryption, the legacy code `auto_i_dat_soap`), its Datenname-shaped Protocol methods and
  `scripts/verify_auto_i_dat.py` were retired by KAN-550 (ADR-111); `zeep`, `requests` and `urllib3` left the
  dependencies with it.
- **Provider captions are vocabulary, global, fetched on «Test connection» only** (ADR-111 (3); KAN-547, Anto
  2026-11-07: no scheduled refresh — a background job has no user to enrol). `integration_provider_caption`
  (`app/integration/models/caption.py`, migration `3f1c9a7e2b54`; in the integration registry, not vehicle,
  because the one action that fetches them runs there): one row per (provider, caption group, list
  `passenger|utility|moto|global`, code) with `label_de/fr/it/en`; the German body of the access check is
  stored and committed, then French, Italian and English are three more `caption_code` rows in one block, each
  language committed as it lands (a failure loses only the languages after it); a failure there leaves the
  connection `connected`. Two concurrent tests of two Betriebe meet on the shared table in a savepoint, never a 500. Other contexts read them through
  `integration.public.get_captions` as plain data — to show a provider's wording
  beside an unmapped code, never to fill a reference list (ADR-044).
- **Catalogue browse is a live identAPI query, bounded** (ADR-112; KAN-548, closing KAN-130):
  `services/catalogue_browse.py` asks the Betrieb's own connection for the brands of a vehicle kind
  (`GET /v1/catalogue/brands?vehicleKind=`), a brand's model groups (`/model-groups?brand=&mode=`,
  `onlyInProduction` in `build` mode — PRD `NurNeue` applies to the model-group list only: the
  identAPI's variant rows carry no filterable production end, so a page of variants is never
  narrowed by the mode), one provider page of variants
  (`/variants?vehicleKind&brand|modelGroupKey&cursor=<page>`, the identAPI's 10; `total` from
  `includeCount`, `nextCursor` the next page number) and the facet values of a fixed tuple of
  fields (`/facets`, `fields=<field> distinct`, **one request per field, only once a brand is
  chosen and the user clicks the views-and-filters control open in that scope** — a kind, brand or
  model-group pick costs its page and its model-group list, the facets wait for the next click on the
  control) — never a page or a field the user did not ask for
  (`tests/architecture/test_catalogue_browse_is_a_bounded_live_query.py`;
  `test_no_bulk_catalogue_retrieval.py` refuses `page[number]` outside the adapter and any catalogue
  query — the adapter's or the service's — inside a `for`/`while`/comprehension, `compute_facets`'s
  loop over the fixed field tuple allow-listed by name with its bound pinned). A query without a
  brand or a model group is 422 before anything is sent, as is a brand or model-group key that is
  no name (`..` or `.` alone, a slash, a query character — the adapter percent-encodes the path segment too); a
  kind or a filter value the map does not know is the user's 422, decided **before** the gateway
  block, so it charges no breaker and writes no call-log row. The grid asks for the next page only
  after the user moved in it (the kit's within-ten-rows prefetch would otherwise walk the identAPI's
  ten-row pages) and never refetches a page on window focus. Every variant a page returns is stored once through `store_seen_variants` (master data,
  ADR-075); a second browse of the page asks again (no mirror), `GET /catalogue/variants/{id}`
  reads the stored row for every Betrieb. Filters are the identAPI's own: a canonical code is
  translated to the provider's through `ProviderCodeMap` (the reverse map — every provider code the
  value maps from, `eq` for one, `in(…)` for several; an unmapped value is 422, a facet value the map
  does not know is a mapping gap and is not offered), numbers only on fields
  the identAPI marks filterable (`kw`, `displacement`, `doors`, `seats`, `co2` — never `ps`, the
  list price or the production years), free text as `has(descriptionShort,…)`, sort on the same
  fields; «ist nicht» (KAN-476) is gone from the catalogue (the identAPI has no «not equal»).
  **States, never errors** (Anto, 2026-11-07: a normal answer, no 503): no connection →
  `notConfigured` (the screen says «Kein auto-i-dat-Zugang» and offers the manual configuration),
  the provider's 403 or a `ident_basis` option not granted → `notInContract`, a failure or timeout
  → `unavailable`; each a 200 with `state` on it, `browseAvailable` = `state == ok`. The demo
  provider needs the real provider's code map copied (`scripts/seed_demo_provider_code_map.py`,
  once per local or staging database) or every kind and code is a gap.
- **The shared identity lookup carries master data for every dealer** (ADR-112 (3), ADR-075;
  KAN-548 closes KAN-130): `GET /v1/vehicle-mdm/lookup` answers `catalogueVariantId`, `listPrice`
  and `specification` (the ADR-071 spec block) for a vehicle already known, with or without a
  provider contract — never options, colours, tyres or images (ADR-013;
  `test_shared_identity_lookup_response_shape.py`, `test_shared_lookup_carries_master_data.py`).
- Provider text (option names, colour names) is stored and rendered **as delivered**, never
  translated (ADR-044); only our canonical reference data is translated.
- **Provider specification excerpts never leave this private repository** (identAPI test
  agreement §5, §12; KAN-491, repo private since 2026-10-28): the code-map transcription
  (`7c4e9a2b6d13`, from the retired SOAP specification) and docstrings citing spec pages stay
  here — never pasted into a public issue, gist, PR on another repo or chat. Fixtures and the
  mock use values that read as invented (`Testprojekt A`, BenutzerNr 9001, `test-dealer`;
  mock OptKeys 9xxxxx; the mock's plates and its one VIN, `WVWZZZDEMV0000001`, resolve to invented cars),
  never a value from an account sheet or a specification example. The entitlement probes'
  inputs (`auto_i_dat_ident.py`: an FzKey, two Typenschein numbers, a VIN) are the examples of
  auto-i's **publicly served** OpenAPI document, not of the confidential PDFs — public, not an
  account value (KAN-488; the test fake routes on the adapter's constants for the same reason).
- **No MCP for provider data** (identAPI test agreement §3; KAN-490): provider-licensed
  vehicle data is never exposed through an MCP server, a tool manifest or any agent-facing API.
  KAN-227's planned AI prompt hook (`vehicle_listing`, not built) may use provider data only
  inside the tenant's own request and response, never as a tool the model can call to fetch more.
  `tests/architecture/test_no_mcp_server.py` refuses an MCP SDK import or a server/tool
  declaration under `app/`, an MCP manifest under `app/` or `frontend/`, and an MCP dependency in
  `pyproject.toml` or a frontend `package.json` that its `ALLOWED_MCP_DEPENDENCIES` does not
  name with a reason (empty: Anto's ruling by name). The repository's own Claude Code tooling
  (`.claude/`) is not the application.
- What has run against the **real** auto-i service account (2026-11-07, from a cloud session,
  outside the app): metadata discovery, one User Enrollment token request and one
  `GET /caption/code?lang=de` (200, 23 code groups). Everything else provider-backed has run
  against the mock or a fake `httpx` transport only. Say so in any claim about real provider
  behaviour.

## Configurator (ADR-068 … ADR-072)

- **ADR-068** — a configuration is a first-class entity with its own ID, referenced by an
  offer, a stock item, a valuation or a vehicle. No list screen, no nav entry.
- **A configuration no host ever took is deleted 30 days after it was created; one a host took
  is kept for good, released or not** (KAN-270; Anto, 2026-11-03, option A, then C).
  `vehicle_configuration.first_taken_at` is stamped — never moved, never cleared — by vehicle's
  consumers `vehicle.configuration_taken_by_*` (`app/vehicle/consumers.py`) of the hosts' own
  facts, each carrying `configurationId`: `sales.offer.configuration_attached` /
  `sales.contract.configuration_attached` (published whenever an offer or contract starts holding a
  configuration id — Path B, the trade-in valuation, a copied offer, a contract from its offer;
  `slot` `vehicle` | `trade_in`), `inventory.stock_item.added`,
  `inventory.stock_item.configuration_attached` («Konfiguration zuordnen») and `valuation.created`.
  The daily job `vehicle.configurations.purge_unattached` (`app/vehicle/daily_jobs.py`,
  `services/configuration_retention.py`; registered last) walks every unstamped configuration in
  batches, asks Sales, Stock and Valuation through their read-only `referenced_configuration_ids` which
  they name now, counts one whose `vehicle_id` links a car in the register as the vehicle record's
  (held for good, whether or not another host ever took it — Anto, 2026-11-05, KAN-567 option A),
  stamps every held one (the safety net for a fact not consumed yet, and for every configuration
  held before KAN-270), and deletes one past 30 days that nothing holds — with its options, an audit
  row (`delete`, reason `never_taken_purged`, never the VIN or mileage) and
  `vehicle.configuration.discarded` — in its own transaction, logging `purged=… stamped=…` — and
  blanks the personal values its earlier audit rows copied (`vin`, `stammnummer`, `licencePlate`,
  `mileageKm`, `notes`, `vehicleId` — and the snake_case spellings rows written before KAN-346 used —
  → `[redacted]`, the key kept; one `redact` row naming rows and
  keys) through `app.core.audit.redact_audit_values`, the log's one sanctioned rewrite (ADR-121,
  KAN-564; Anto, 2026-11-07); the discard of a host's failed copy (KAN-188) does the same. A
  configuration a host took is never deleted, so its history stays whole (FR-C-11). One a
  host let go before KAN-270 left no trace and goes like a never-taken one. Residual race (the
  service's docstring): one past 30 days that a host attaches in the moment the job runs is deleted
  under it, and the host's reconciliation reports the dangling reference. A copy (FR-C-11) is a new
  configuration, unstamped until its host takes it.
- **ADR-069** — the configurator is provider-backed (reverses PRD-Sales v2 S-D05). Manual
  configuration stays as the off-catalogue path: grey imports, oldtimers, pre-1982 vehicles,
  anything auto-i-dat does not cover.
- **ADR-070** — a configuration **never writes vehicle-mdm** (architecture test). It attaches
  to a pipeline stock item, a valuation, or an existing vehicle when a VIN is known.
- **ADR-071** — one specification block, three carriers: the catalogue variant, the
  configuration, and the host's frozen snapshot. A field on one and not the others is a defect
  (`test_spec_block_carriers_do_not_drift.py`). `ModelVariant` and `VehicleConfiguration`
  carry it; the offer's (and a contract's, KAN-67) frozen `vehicle_snapshot` (ADR-041,
  `sales/services/snapshot.py::freeze_vehicle_snapshot`) carries it under `spec` when Path B
  attached a configuration (KAN-10).
- **ADR-072** — option packages, exclusions and extra conditions are stored and shown, never
  enforced: a conflict warns, it never blocks.
- **Host / mode matrix** (PRD v1.4; the pending contract added by KAN-67 and ADR-083 —
  PRD-Configurator's matrix not yet amended): Stock → add to pipeline: both (`build` factory order, `record` bought
  in) · Stock → «Konfiguration zuordnen» on an item without one: both, `record` first, its VIN
  prefilled (KAN-463) · Stock → «Konfiguration bearbeiten» on **every** stock item with one, in the pipeline or in stock, that
  no contract reserves (Anto, 2026-11-02, KAN-510 option A; the configuration card, and the purchase
  booking's `configuration_vin_differs` refusal): the configuration's own mode only, its PATCH the only
  write — Stock writes nothing of it. A reserved item shows «Vertrag bearbeiten» instead, disabled with
  the reason: a contract reserves a car only once signed, and a signed contract is frozen (ADR-083), so
  it is cancelled first. The configuration PATCH does not check its host on the server; and a car in
  stock whose configuration's VIN is edited then differs from the stock item's (KAN-544) · Offer and pending contract → new configuration: `build` only · Offer and pending
  contract → trade-in: `record`, via the valuation path · Valuation → new valuation: `record`
  only. The configuration entity
  supports both modes; the host decides which is reachable. Never bake a mode switch into the
  offer overlay. Each host enforces it at its API (`configuration_mode_not_allowed`, 422) and
  the overlay takes `allowedModes`; hosts read a configuration only through
  `vehicle.public.get_configuration_for_host` (KAN-10) — and Stock's nightly label refresh
  through `vehicle.public.get_configuration_labels`, labels only, each read in the Betrieb the
  stock item names (KAN-534); a host that copies itself (Sales'
  Save-and-copy) gets its own copy through `copy_configuration_for_host`, undone by
  `discard_configuration_copy` (FR-C-11, KAN-188).
- **The configurator is a Sales-module surface** (KAN-198, D-A-04): every route of
  `api/configuration.py` and `api/identification.py` sits behind
  `require_entitlement(ModuleCode.SALES)` at router level — 404 for a Betrieb without Sales,
  before any role check (`tests/architecture/test_sales_routers_carry_entitlement_gate.py`).
- **Identification (FR-C-02, KAN-42; live on the identAPI since KAN-547)** is
  `services/identification.py`, one input (`GET /v1/vehicle-identification`): VIN → vehicle-mdm,
  then — only where the connection's probed `vin` row is granted (KAN-549; not in the test licence, §8 /
  ADR-113) — `identify_by_vin`, read like a Stammnummer answer (one car with its variant is one candidate,
  several the picker, never the plate cache); not granted (no row is not granted) → the note
  `vin_decode_not_entitled` («VIN-Abfrage nicht im Vertrag …», shown) and no call; a VIN the provider does not
  know → no match, the VIN kept for the manual path; its 403 at call time → the same note;
  Kontrollschild → the provider first (ADR-114, never vehicle-mdm's plate register) behind the
  per-tenant plate-lookup cache (30-day TTL, daily purge, never enumerable): `identify_by_plate`'s
  Vehicle Build answer carries the registered car **and** its catalogue variant, so one registered
  car is one candidate without a second call (its observed identity carries the registered car's VIN,
  Stammnummer, Typenschein and first registration — the configuration taken from it stores them),
  several are the picker — every car the answer names a Typenschein for, whether or not the account
  sees its Stammnummer (`vrn` "depends on user group definition"; `PlateRecordRead.stammnummer` is
  nullable), never one chosen for the advisor (a Wechselschild informational, one Stammnummer under two
  Typenscheine a conflict and a `vehicle.plate_lookup.conflicted` event). The cache is filled by a
  plate lookup only, whole or not at all (an answer with a record it cannot store is not cached: a
  part would later read as the whole truth about the plate); a `vrn` answer never fills it. A cache
  hit answers from the stored variants and asks the provider only when none are stored, so a repeated
  plate within 30 days makes no call — the cache keeps the Typenschein, not the FzKey the live answer
  named, so where several stored variants share the Typenschein the cache hit is a picker with the
  best match offered where the live answer was one candidate (remembering the FzKey is a migration,
  Anto's call; KAN-592); Stammnummer → vehicle-mdm, then `identify_by_vrn`
  (live), then the cache as the fallback; Typenschein → `find_variants_by_type_approval` (one page)
  merged with the stored variants of the number; Werkscode → `find_variants_by_werkscode` merged the
  same way. Every variant a rung returns is stored through `upsert_model_variant` (master data,
  ADR-075; each upsert in a savepoint, a concurrent first lookup re-reads the winner's row) — the
  stored rows are a cache of what was seen, never the source. The provider's refusal of
  the `ident_basis` option is the note `provider_not_entitled`, a failure or timeout `provider_unavailable`
  (the plate rung keeps KAN-42's `plate_lookup_not_entitled` / `plate_lookup_failed`), rendered as hints in
  the configurator (`configurator.identify.notes.*`), never errors; the rung then falls through to what
  we hold. Several hits are a picker, never a choice (name, trim, production years, power, list price);
  the best match (`find_best_match`, Vehicle Match by Typenschein + Neupreis) is a proposal, confirmed
  through `confirmedBestMatchCode`. The Vehicles list's FR-V-06 picker shows a Wechselschild in the same
  informational blue (ADR-114, KAN-191 q2). A configuration's read carries `energyRating` — the variant's
  rating for the first-registration year, else the model year, else the newest held (KAN-479 option A,
  `energy_rating_for_configuration`), shown with its year in the shared summary card's «Verbrauch &
  Energie» group and so on the stock DetailsTab.
- **The five coded fields** (`vehicleKind`, `fuelType`, `bodyStyle`, `drivetrain`,
  `transmission`) are never empty by an advisor's hand (Anto, 2026-10-12, KAN-247/246): on a
  provider configuration they are locked — read-only on screen, a PATCH that changes one is 422
  `coded_field_locked` (the same value is accepted); a manual configuration needs all five to be
  created (422 `coded_fields_required`) and cannot clear one (422 `coded_field_required`). They
  and the spec-block reference fields take only an active value of their reference list
  (KAN-96, KAN-245). Every other nullable observed or spec field clears with an explicit
  `null` on PATCH; an absent field is left alone (KAN-247). `catalogueVariantId`,
  `catalogueMatchStatus` and `spec: null` still mean "leave alone"; the lock follows `source`.
- **Re-sync (FR-C-16)** is only ever the advisor's request (`/v1/configurations/{id}/resync`):
  disagreeing fields listed, overrides marked, only chosen fields applied.
- **Configuration audit rows name a field by its camelCase API name** (`firstRegistrationDate`,
  `mileageKm`) in every event — create, update, match-status change, re-sync, catalogue fill
  (KAN-346); rows written before keep their snake_case keys (the log is append-only).

## auto-i-dat licence constraints (KAN-484)

The identAPI test agreement of 2026-10-28 is confidential (§12): it lives in Anto's Drive and is
never quoted in the repository, a ticket or a prompt. What it and Anto's rulings
(2026-10-28/29) mean for the code:

- **Single lookups only, for every provider** (§2, §9; KAN-485, ADR-023 as amended 2026-10-29):
  no full seed, no daily delta, no `FzKeyChanged`, no daily job reaching a provider — the demo
  provider (`auto_i_dat_mock`) follows the same rule. A variant enters the catalogue only through
  identification (every rung stores the variants it returns, master data only — KAN-547; a plate
  lookup also fills the plate-lookup cache) or a browsed page (KAN-548, the same store); since KAN-547 every
  identification rung stores what it finds and since KAN-548 every browsed page does too (the browse itself is
  live, never a mirror read). **Its licensed details load once per Betrieb when the car is opened** (KAN-211,
  ADR-123; `services/catalogue_details.py`): the specification read (`GET /v1/catalogue/variants/{id}/specification`
  and `/vehicle-mdm/{id}/catalogue-specification`) asks the Betrieb's provider for every detail it has not
  stored — colours (with the variant), options, tyres, images, energy label: five requests on the first open,
  none on the second — each detail its own `call_capability` block, savepoint **and commit**, ending in its own
  `VariantDetailState`: `present` (loaded, possibly empty; also the provider's coherent "nothing": a key it no
  longer lists, a 400/422 — `error` says which, and the detail is not asked again until «Neu laden») ·
  `not_in_contract` (403, or a licence option «Test connection» recorded as absent — then nothing is sent; asked
  again only once a later probe records the option as granted) · `unavailable` (timeout, 5xx, open breaker; asked
  again on the next open) · no row = never asked (no connection, or no key at this provider). A refused or failed
  detail never discards the car or the others (KAN-212); what a provider sends is stored defensively (a key repeated
  in one answer is one row, texts are cut to their columns, an over-long image URL is left out). A transaction-scoped
  advisory lock on (Betrieb, variant), taken per detail on the request's own connection (`pg_advisory_xact_lock`,
  `lock_timeout` 25 s — past it the second open leaves the rest to the first) and released by that detail's commit,
  serialises two opens of the same car by the same Betrieb so two tabs send one set; the load holds no connection
  besides the request's own (the gateway's brief session aside); another Betrieb's open of the same car waits only
  where both write the same shared row — a new mapping gap, a price year, this year's label — and the loser's unique
  violation is never a 500: `_record_mapping_gap` re-reads the winner's gap row and counts its occurrence there; the
  colours request's master-data visit and the energy-rating insert run in a savepoint and let the winner's row stand. The specification's `details.*.note` says why a state is what it is
  (the failure's class, `entitlement_absent`, `variant_unknown_at_provider`, `energy_label_ambiguous`). The configurator's specification query neither refetches on window
  focus nor retries (a state, not an error). No automatic refresh of any kind: `POST /v1/catalogue/variants/{id}/details/reload`
  («Neu laden» on the stock car's technical data, dealer manager or platform staff, audited as
  `vehicle_variant_details.reload`) is the one refresh, one fresh request per detail replacing the Betrieb's rows.
  The energy label is stored once for every Betrieb as that year's `ModelVariantEnergyRating`
  (`source = auto_i_dat`, filling an absence only; ADR-042 — Anto, 2026-11-08), and only for a year whose rows
  (one per gearbox) agree on the class — a disagreement stores nothing and is noted; the state stays per Betrieb.
  Options carry the manufacturer's code (`manufacturer_code`, the identAPI's `optionCode`) beside auto-i's key
  (`option_code`), their `group` resolved through the code map like every coded field (a miss is a gap and a flat
  list; the raw code stays on the row — `option_group_code` — and the admin's resolve fills every Betrieb's options
  at once, `apply_resolved_code_to_option_groups`; the demo provider's identity rows come from
  `scripts/seed_demo_provider_code_map.py`), their relations
  (`contains` / `excludes` / `price_in_combination_with` — the last only where the provider names a price) stored
  and shown, never enforced (ADR-072), and the year they stand for (the one asked, or the current Swiss year for a
  car still built).
  **On screen** (KAN-211 PR 2, KAN-212): each tab of the configurator shows its detail's state through the one
  `DetailStateNotice` — «Nicht in Ihrem Vertrag» (`notInContract`), «vom Anbieter derzeit nicht verfügbar»
  (`unavailable`), «noch nicht geladen» (`notLoaded`); a loaded detail shows its data and nothing else, unless its
  note says the provider answered a coherent «nothing» (`variant_unknown_at_provider` on the colours,
  `energy_label_ambiguous` on the label): then one translated line says so, and a failure's class name never
  reaches the screen — and the
  options tab offers a search over text, manufacturer code and auto-i key (every option of the opened car is loaded,
  Anto 2026-11-08). The colour's **name is shown as delivered and its type is the advisor's pick** (the identAPI
  carries no finish): `exterior_colour_type` from the `colour_type` list, `interior_colour_type` from the
  `interior_colour_type` list (platform `4c8e1f2a9d57`: Stoff, Leder, Teilleder, Alcantara, Kunstleder,
  Sonstiges), two nullable columns on the configuration (vehicle `a7c2d94e1b35`) validated like every coded
  field — the one field whose name is not its list's, `validate_reference_codes(..., list_codes=)` — and read
  beside the colour on every summary card («Deep Black · Metallic»). «Neu laden» (`CatalogueDetailsReload`) sits
  under the stock car's configuration card for a dealer manager or platform staff and lists each detail's state
  after the reload.
  `tests/architecture/test_no_bulk_catalogue_retrieval.py` guards the rule and lists the five detail
  operations of the adapter, `load_on_open`, `reload_details` and `get_catalogue_specification` as queries no
  loop may call. The
  bookkeeping table those runs wrote (`vehicle_provider_sync_state`) and the platform's
  catalogue-sync status board that read it are gone (KAN-497; Anto, 2026-10-31, option A —
  vehicle-chain migration `e4b7c2a91d36` drops the table).
- **Every provider request is counted** (§6; KAN-486): one `integration_call_log` row per
  request — a retry is a second row — with the provider's own operation name as `capability`
  (the identAPI operation — `caption_code`, `vehiclecatalog`, …; `FtpUpload` for the AutoScout24 feed) and
  `cost_units = 1`. An adapter wraps
  each request in `integration/services/metering.py::provider_request`; a `call_capability`
  block that metered none writes its one row under its label, as before; the circuit breaker
  stays per block, and a block that fails after its last answer arrived marks that request's
  row `ERROR`. **Nexotec enforces no quota and sends no usage warning**: auto-i-dat limits its
  own access (Anto, 2026-10-29). The identAPI's go-live check is «Test connection» itself (the
  SOAP-era `scripts/verify_auto_i_dat.py` went with KAN-550).
- **Looked-up catalogue data is kept and shared** (Anto, 2026-10-29, KAN-487): what a single
  lookup stored in the catalogue tables is kept indefinitely — no retention window — and variant
  master data stays readable by every Betrieb (ADR-075). The plate-lookup cache keeps its 30-day
  TTL (KAN-487's «Do not touch»). What is never done is retrieving or storing the whole catalogue.
- **The test access never runs in production** (§1; KAN-489): it is a `sandbox` connection, and
  on a deployment whose `DMS_ENVIRONMENT` is anything but `development`, `local`, `test` or
  `staging` (fails closed; an unset variable reads as `development`, so production must set it)
  the gateway refuses every call on a sandbox connection before the provider is reached (`SandboxConnectionOnProductionError`,
  audit action `sandbox_refused_in_production`; "Test connection" turns the card's badge to
  Fehler — the card does not show `last_error`'s reason). The
  auto-i-dat Connect dialog's environment hint says it: a service account only, never the
  personal token, test environments only. There are no licence types and no licence end dates
  (Anto: a connection auto-i-dat switches off shows as failing; the dealer renews with
  auto-i-dat).
- **No MCP for provider data** (§3; KAN-490).
- **The test licence covers the identAPI only**, and the identAPI is the one provider since
  KAN-488 (ADR-111; the login and transport paragraph above). The service account runs as a
  `sandbox` connection on local and staging only (ADR-113). VIN queries are not in the licence
  (§8): the `vin` option is probed (on `/vehiclebuild/vin`, the rung's own endpoint) and its
  `granted = False` row — or no row at all — is what keeps the VIN rung (KAN-549) from ever calling it.

## auto-i-dat facts that are easy to get wrong

- **VIN identification is an identAPI licence option** (`vin`; ADR-113). Built by KAN-549 on Vehicle
  Build (`GET /vehiclebuild/vin/{vin}`), not on `GET /vinrequest/{input}` (Anto, 2026-11-09, option A).
  `integration.public.vin_decode_granted(connection_id=)` reads the connection's probed `vin` row and
  nothing else, strictly (no row is not granted — the opposite of every other option);
  `tenant_has_capability("vin_decode")` asks the same of the tenant's enabled connections. The SOAP-era
  separate provider for VIN and its derivation from that connection's health are gone (integration
  migration `3b3faa39c5e2` deleted its provider row, connections and cached rows).
- **Typenschein** — `vehicle_type_approval` is many-to-many with `vehicle_model_variant`;
  `type_approval_number` is indexed, not unique. Use `find_model_variants_by_type_approval`
  for the reverse lookup. A variant mirrored before KAN-42 has no links; identification's best
  match visits an existing variant with `upsert_model_variant`, which links them (as the
  colours request on open does too, KAN-211) — no backfill asks a provider for them
  (KAN-190, KAN-485).
- **A motorcycle's `drive` is 2-Takt / 4-Takt / Kein Takt** — a stroke count, not a
  drivetrain (a car's and a light commercial's are Hinten/Vorne/Allrad). The seeded code map (migration
  `7c4e9a2b6d13`, transcribed from the retired SOAP specification's CodeGrpNr 112 / 012 / 022 tables and still the
  identAPI's live map — Anto, 2026-11-07, KAN-547 decision 1; asserted in `tests/test_provider_code_map_seed.py`
  and checked after every CI migration run) maps kind `03`'s codes → `engine_cycle`; KAN-550 kept it (Anto,
  2026-11-09, D1). **The sync routes it by the resolved vehicle kind (KAN-547, closing KAN-447):** the identAPI's
  `type` is unambiguous, so `catalogue_sync._raw_codes` reads a motorcycle's `drive` as `engine_cycle` and leaves
  its `drivetrain` NULL, and every other kind's as `drivetrain` with no `engine_cycle` — routed on the **resolved**
  `vehicle_kind` (`motorcycle`), never a raw code; while the kind is itself a gap, `drive` is recorded as `drivetrain`,
  as before, and moves once the kind resolves to a motorcycle and the variant is visited again — the `drivetrain`
  gap that visit filed stays open in the queue (resolving it would map a stroke count into the drivetrain list, R-C-5's
  hazard; unreachable for `auto_i_dat`, whose three kinds are seeded). A row without a `type` resolves no code and
  files no gap (the vocabulary is per kind); its raw codes are kept under kind `""` and a warning names the FzKey.
  The seeded map needs no re-key: the identAPI's codes are auto-i's own (body 1/3/4/5/6, fuel 1…16, drive 1/2/5
  and 2/4/9, gearbox 1/2/5/6, consumption norm 3/4, type 01/02/03) — Anto, 2026-11-07, KAN-547 decision 1.
  **The spec block's reference fields are code-mapped too** (`_CODED_FIELDS`: `vehicle_class` ← `category`,
  `emission_standard` ← `euronorm`, `consumption_norm` ← `consumptionNorm`, `valuation_classification` — no
  identAPI counterpart, always NULL from it); `category` 1–6 has no seeded rows and the `vehicle_class` list holds
  homologation classes (M1, N1, …), so every category is a mapping gap until the list question is settled
  (follow-up ticket). **A lookup stores the whole block** (`_FILLABLE_MASTER_FIELDS` = the spec block, fill-if-NULL
  on a revisit, KAN-83), the list price per model year (`vehicle_variant_price`, `source = provider`, a held year
  never replaced), and the identAPI's A–G label as **this Swiss year's** `ModelVariantEnergyRating` with `source = auto_i_dat` — never
  over a row already held for the year, a BFE row included, and only for a letter the `energy_efficiency_category`
  list carries (ADR-042 amended by ruling: Anto, 2026-11-07, KAN-547 decision 4; a later BFE import rewrites such a
  row's value **and** its `source`). Two block fields the identAPI does not carry stay NULL: `system_kw`,
  `valuation_classification`; `euronorm` has no seeded map rows yet, so it is a gap on every lookup until mapped.
- **`ProviderCodeMap` is keyed `(provider, vehicle_kind, code_group, provider_code)`** —
  `vehicle_kind` is the raw FzArt, `code_group` the semantic list code, `provider_code` the raw
  code; never a numeric CodeGrpNr (`resolve_provider_code` refuses one; migration
  `ff5b97c11665` re-keyed the open legacy gaps that `7c4e9a2b6d13`'s CodeGrpNr table maps, KAN-194). An unmapped code of a code-mapped field (the variant's
  `vehicle_kind`, `fuel_type`, `body_style`, `drivetrain`, `transmission`; an option's
  `equipment_feature`; a colour's `colour_area`, a tyre's `axle_position` and `tyre_type`, KAN-75)
  becomes a `mapping_gap` (FR-C-10), never a silent default. **A code the provider did not send at
  all is no value and no gap** (Anto, 2026-10-28, KAN-75 option C): a colour without `FarbArt` is
  hidden, a tyre without an axle reads «Nicht zugeordnet». Revisit once a staging auto-i-dat account
  shows whether it happens.
- **Keep the provider's full image URL** (`ImageRef.image_url`); the provider returns a
  directly fetchable URL per image — no proxy, no base-URL reconstruction.
- Stammnummer resolves against vehicle-mdm, then the identAPI by `vrn` (= Stammnummer), then the
  plate-lookup cache. «auto-i-dat accepts no Stammnummer search» was a SOAP-era reading, withdrawn
  (ADR-111) and built live by KAN-547. The
  catalogue sync links each variant to its Typenscheine
  (`vehicle_model_variant_type_approval`, model `VariantTypeApproval`, KAN-42).

## Catalogue administration

The mapping-gap queue has a screen (`components/MappingGapsQueue.tsx`, on
`/catalogue?tab=mapping-gaps`); reference data has `/settings/reference`; brands are created and
renamed on `/catalogue?tab=brands` (`components/catalogue/BrandsAdmin.tsx`, KAN-448) — a tab
platform staff alone see, since the brand write is `require_access_role()` (platform admin); the
code is set once, the name is edited with If-Match, a taken code is 409 `brand_code_taken`.

`valuation` is its own context — see `.claude/rules/valuation.md`, not this file.
