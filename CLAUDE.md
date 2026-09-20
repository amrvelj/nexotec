# Nexotec — working rules

Swiss automotive Dealer Management System. FastAPI + Postgres + React SPA (Mantine,
TanStack Query/Table/Virtual, Lucide). One deployable today.

**Notion is the single source of truth.** This file is a working summary of it. Keep both in
step: a work package is not done until the PRD says what shipped, the Gap Analysis cites the
new head, and any decision taken during the build is an ADR rather than a PR comment.

**Quality before speed.** This product is meant to last. Where a shortcut and a correct
implementation disagree, take the correct one and say what it costs. Do not cut scope, skip
a migration, or lower a bar to protect a date — no date in this project outranks getting it
right.

## Commands

- Install backend deps: `pip install -e ".[dev]"`
- Install frontend deps: `npm install --prefix frontend`
- Run backend tests (SQLite, fast lane): `pytest`
- Run backend tests (Postgres, the lane of record): `docker compose up -d db && DMS_TEST_DATABASE_URL=postgresql+psycopg://dms:dms@localhost:5432/dms_platform pytest`
- Run a single test: `pytest tests/test_customer.py::test_name`
- Run migrations: `alembic upgrade heads` (plural — one chain per context since PR-3, ADR-015; `head` either fails or silently applies only one context)
- Start the whole stack from cold: `make up` (generates a dev-only `.env` on first run)
- Start the backend alone: `uvicorn app.main:app --reload`
- Start the frontend alone: `npm run dev --prefix frontend`
- Backend lint: `ruff check .` · backend typecheck: `python3 -m mypy app` — run mypy **as a module**; the bare `mypy` binary resolves a different interpreter and reports phantom import errors
- Frontend lint: `npm run lint --prefix frontend` (oxlint) · typecheck + build: `npm run build --prefix frontend`
- Import boundaries: `lint-imports`
- Regenerate the frontend's API types from the backend's own OpenAPI schema: `make generate-frontend-types` (or `npm run generate:api-types --prefix frontend/apps/dms`) — both run the same two commands, so they can't drift from each other

CI (`.github/workflows/test.yml`) runs eleven jobs: secret-scan, import-linter, ruff, mypy,
frontend, docker-build, migration-smoke-test, migration-upgrade-from-previous,
outbox-worker-smoke-test, pytest on Postgres, and frontend-openapi-drift. **Whether they
block a merge is a branch-protection setting on `main`, not something this repo can prove.**

## Authority

The binding specification lives in Notion. This file is a working summary of it.
**Where this file and Notion disagree, Notion wins — stop and say so, and correct this file
in the same session.**

- Target Architecture — binding · **ADR-001 … ADR-072**
  https://app.notion.com/p/3b73e79334dd810faf92dddf9268d29b
- **UI/UX Specification — the one UI page** — binding for anything a user can see, and it
  **wins over a module PRD on presentation**
  https://app.notion.com/p/3b53e79334dd81b2a18ed1540054d175
- Build Sequence **v2.5** — the work packages, their exit criteria, their prompts, and the
  **audited status table** recording what actually shipped
  https://app.notion.com/p/3b73e79334dd810496a7f7342ba86f13
- Gap Analysis — Target vs. Shipped Code
  https://app.notion.com/p/3b73e79334dd81cbb181f85d58471d52
- Risk Register
  https://app.notion.com/p/3b73e79334dd8164a502cd06520b7993

Module PRDs, when you are building against one:
`Customers` https://app.notion.com/p/3b53e79334dd80c4a7e2e92fc3a85986 ·
`Vehicles` https://app.notion.com/p/3b73e79334dd81028e76f3bdd24beeff ·
`Stock` https://app.notion.com/p/3bb3e79334dd8073be48d91997001fae ·
`Sales v2` https://app.notion.com/p/3bb3e79334dd80cd89eee23899f031e6 ·
`Configurator` https://app.notion.com/p/3cf3e79334dd80c4af05ddb042da7d9d

Read Notion when this file does not cover the decision in front of you. Do not read it
as a warm-up.

> **The Target Architecture page still contains a "Roadmap to the target" table with
> Stages A–F. That table is SUPERSEDED by ADR-015.** Stages C, D and E describe service
> extractions that are not happening. Ignore them.

### This file lives in the repository, and only here

There was a second `CLAUDE.md` in the Nexotec Drive folder, and it was the better-maintained
of the two. **It was never read by anything.** Claude Code reads `CLAUDE.md` from the working
directory it is launched in and from that directory's parents — never from Drive, which is
just a mounted folder it can open when a prompt hands it a path. The repository copy is the
one that takes effect, so the repository copy is the one to keep current.

If you find two copies disagreeing, **this one wins** and the other should be deleted rather
than reconciled. The prototype is the opposite case and stays in Drive on purpose — see below.

## Anything a user can see

This section exists because the failure it prevents is silent: a screen that works, passes
review, and looks like it came from a different product.

**Two documents are binding, in this order.**

1. `UI/UX Specification — the one UI page` (linked above). Design tokens, the shell, the
   action bar, the data grid, detail screens, forms, the component contracts and the screen
   inventory. It **wins over a module PRD on presentation** — a PRD says *what* a screen
   does, that page says *how*.
2. **The interactive prototype**, in the Nexotec Drive folder at
   **`dms-platform/ui-prototype/nexotec-prototype.html`**. Six review rounds with the product
   owner. It is the **reference implementation**, and it is normative for presentation.
   The absolute path, because this is a Mac and Drive is an ordinary mounted folder:
   `"/Users/antomrvelj/Library/CloudStorage/GoogleDrive-mrvelj.anto@gmail.com/Meine Ablage/1. Persönlich/11. Arbeit/Anto/Nexotec/dms-platform/ui-prototype/nexotec-prototype.html"` — quote it, it contains spaces.

**Where the spec page and the prototype disagree, that is a defect in one of them. Stop and
ask. Do not pick whichever is closer to hand.**

**How to actually use the prototype.** Open it in a browser and click through the screens you
are about to build. Do **not** read the built file as text — it is ~860 KB of concatenated
output and reading it will consume your context for nothing. The readable source sits beside
it in `src/`, roughly 12'000 lines across ~25 small files, and it is greppable: search for
a route (`#/valuations`), a component (`NX.rowGroup`), or an i18n key (`val.st.expired`).
`README.md` in the same folder explains what each pass changed and why, and `HOW-TO-USE.md`
is the short version of this paragraph.

*Why the prototype is in Drive and this file is not:* the prototype is maintained from the
spec side, and whoever updates it can write to Drive but not to your clone — a copy in the
repo would be one forgotten `cp` away from being a stale second source. `CLAUDE.md` is the
reverse: it is an instruction file the tool reads automatically, and the tool looks in the
repository. Putting it in Drive is how it silently stops taking effect.

**Screen components come from WP-6c and are not rebuilt per module.** If you find yourself
writing a grid, a filter control, a detail-screen header, a picker, a form dialog or a
document renderer inside a module package, you are in the wrong package. Use the library. If
it cannot do what you need, that is a WP-6c change plus an ADR — never a local component.

The UI rulings that bite most often, so you recognise them before you reach the page:

- **ADR-056** — grid state (search, filter, sort, tab, scope) lives in the URL, and the URL
  is the shareable unit. Layout and density stay out of it; those are the reader's own
  ergonomics, and they belong on the user preference record.
- **ADR-058** — views and filters are **one control**, labelled with the current view. Not
  two buttons. A user-defined filter is **one predicate** — field, operator, value — never a
  compound expression typed into a box.
- **ADR-059** — opening a record from inside a process renders it as an **overlay** on top,
  not a navigation. Losing a half-built offer is a defect, not a trade-off. The naive
  implementation (set the hash, set it back) fires the router and destroys the screen
  underneath, which is the exact bug the component exists to prevent.
- **ADR-060** — every persisted field is available as a grid column. A documented subset is
  visible by default. A field that is stored and cannot be put on screen is a defect.
- **ADR-061** — every detail screen: **one primary action, one alternative, and an overflow
  carrying the entity's full row menu** — same items, same order, same
  disabled-with-explanation entries. The row menu is the single definition of what can be
  done to an entity; both surfaces render from it.
- **ADR-063** — generating an offer is **two steps**: build, then review the rendered
  document in the customer's correspondence language with the seller-only margin panel
  *beside* it, never on it.
- **i18n is not a late pass.** DE/FR/IT/EN are all first class, a missing key renders a loud
  marker and never a German fallback, and the **customer's correspondence language is not the
  user's UI language** — never the same control, never the same stored field.

> **There is a render-level test harness — `jsdom` + `@testing-library` (react/dom/user-event/
> jest-dom), a dev dependency of both frontend workspaces since 2026-09-04.** The default test
> environment stays `node` for pure-logic tests; a file opts into rendering per-file with
> `// @vitest-environment jsdom`. Look for a sibling `*.render.test.tsx` before assuming a
> component is only checkable by eye — most detail-screen and list-page components already
> have one, and a change to a file that has one gets a render-test update in the same PR, not
> a "verified by looking" claim in the commit message. *(This paragraph previously said the
> harness didn't exist; it went stale the day it landed and stayed wrong for a week before a
> review caught it mid-KAN-58 — check the actual devDependencies before trusting this file's
> claims about test infrastructure.)*

## Where we are

**One deployable, one database, and that is deliberate.** ADR-001 makes true microservices
the destination; ADR-015 rules that extraction happens when a trigger fires, not on a
schedule. Right now we are building **hard seams inside one application**.

Do not create a second deployable, a second database, a service template, a broker, or a
`services/` directory. If you think a trigger has fired, say so — do not act on it.

Extraction triggers (any one): a context needs independent scaling · it needs a different
retention or data-residency regime · another context's deploys keep breaking it ·
engineering headcount reaches three · a provider licence demands process isolation.

## Work-package status — audited 2026-09-03, corrected 2026-09-13

Read this before assuming a package is finished. Every line was checked against the code, not
against a commit message. The full evidence is in the Build Sequence's audited-status table.

> **2026-09-13: this table had gone stale.** A pile of ~30 Kanban tickets sat in "In Review"
> long after their PRs merged — nobody moved them to Done. Auditing that pile (each ticket's
> claim checked against the actual current code, not the commit message) found 26 fully
> shipped and corrected several of the gaps below. **If you're relying on a "NOT BUILT" or
> gap note here for a decision, and it looks surprising, check the code before trusting it —
> this file goes stale exactly this way and won't announce it.**

| WP | Status |
|---|---|
| WP-1 Context seams | **Done.** 8 import-linter contracts with no ignore mechanism, gated in CI · zero cross-context FKs across all 42 FK sites · customer events from the real write path · a redelivery-idempotency test on the Postgres lane |
| WP-2 Platform hardening | **Done.** RS256 + JWKS · roles as a frozenset · `make up` · OTel with `correlationId` in log and span · `/readyz` · Infisical |
| WP-3 Organisation model | **Done.** The one gap this table used to note — the anti-ambient-group-read lint rule matching only `group_id ==` — **fixed 2026-09-13 (KAN-28)**: the rule now covers `dealer_group_id` too, across every spelling (`==`, `.in_()`, `filter_by`, raw SQL), against an exact file allowlist. |
| WP-4 External identity | **Done.** Zitadel authenticates and never authorises; the `credential` table is dropped by migration |
| WP-5 Vehicle three-layer model | **Partial — two of four exit criteria fixed 2026-09-13, two still open.** See below |
| WP-6 Provider gateway | **Substantially done.** Never run against a real auto-i-dat account — no WSDL, no credentials in the repo, only the mock path exercised. The Configurator's own gateway-coverage work (C-0, KAN-38) is separately tracked — see below |
| WP-6b Document template | **Done.** WeasyPrint in exactly one file, enforced by an architecture test |
| WP-6c UI foundation | **Substantially done.** The render-level test harness this row used to say didn't exist landed 2026-09-04 (see the note above) — that's a WP-6c exit criterion, not a gap anymore |
| WP-7 Stock and inventory | **Partial — one of three gaps fixed 2026-09-13, two still open.** See below |
| WP-8 Sales and valuation | **Partial — three of four exit criteria fixed 2026-09-13, one partially.** See below |
| WP-9 Numbering, period lock, invoicing | **NOT BUILT.** `app/finance/__init__.py` is a three-line stub |

### The specific gaps — do not assume these away

**WP-5.** ~~The nightly reconciliation has no scheduler~~ **Fixed 2026-09-13 (KAN-20)** —
`app/worker.py::register_daily_jobs` now calls `reconciliation_runner.run_all`, a real
scheduled caller where only a test existed before. ~~The legacy `vehicle` table was never
migrated off~~ **Fixed 2026-09-13 (KAN-18, KAN-31)** — `scripts/migrate_legacy_vehicles.py`
(dry-run by default), `legacy_vehicle_write_frozen`, and both `app/customer/reconciliation.py`
and customer-side vehicle-party allocation now point at the real `vehicle_mdm` table.
**Still open:** plate lookup returns neither keeper nor open orders ("open orders" does not
exist; `app/aftersales` is a stub) · `catalogue_admin` as a screen-less API — **not
re-verified in the 2026-09-13 audit** (it wasn't in the "In Review" pile that prompted it;
KAN-40's catalogue browse work landed since, so this specific claim may itself be stale —
check before trusting it).

**WP-7.** ~~Landed cost and the fiktiver Vorsteuerabzug never reach Sales~~ **Fixed
2026-09-13 (KAN-25)** — `app/inventory/services/pricing.py::get_stock_item_pricing` now
returns `landedCost`/`notionalInputTax*` alongside the pre-existing fields. **Still open:**
marketplace publishing has no transmission code and no consumer (KAN-27, tracked in Notion as
Ready for Development, not started) · `invoicing_gate.apply_finance_invoice_issued` has zero
callers — not re-verified in the 2026-09-13 audit.

**WP-8.** ~~The offer does not print in the customer's language ... no VAT line on any
document~~ **Fixed 2026-09-13 (KAN-23)** — `build_offer_content`/`build_contract_content`
take a required `language` parameter; the single gross-price VAT line is on the printed
document per ADR-057. ~~`sales.contract.confirmed` carries no pricing snapshot~~ **Fixed
2026-09-13 (KAN-24)** — `_confirmed_event_payload` now builds and includes one. The
`transaction` rows: **`sale`-type rows now migrate for real (KAN-26)** —
`scripts/migrate_transaction_rows.py`, dry-run by default, idempotent, through the real
production write path (9/9 tests passing on Postgres). **`trade_in`-type rows are still
explicitly NOT migrated** — `_migrate_trade_in()` unconditionally rejects every such row
(`rejected_trade_in`), documented in the script's own docstring as not built, deferred to a
separate follow-up because `supplier_is_vat_registered` and vehicle condition aren't
recoverable from the legacy row.

## The twelve bounded contexts

| Context | Owns |
|---|---|
| `platform` | tenants/dealers, users, auth, roles, feature flags, reference data, locations, document templates |
| `customer` | customers (**group-scoped**, ADR-014), **contact channels as child collections** (ADR-067), external IDs, duplicates/merges, `VehicleParty` |
| `vehicle` | physical vehicles, catalogue (canonical taxonomy + the per-tenant provider mirror), plates, odometer, custody, provenance, **the configurator** |
| `sales` | offers and contracts, pricing build-up, trade-ins, the container generation workspace, documents |
| `inventory` | stock items, pipeline vehicles, reservation, Wagenbuch, marketplace publishing |
| `valuation` | the standalone valuation application (ADR-066, FR-V-09/FR-V-17), tenant-private (ADR-029) |
| `integration` | the integration registry — connections, write-only secret refs, entitlements, call log, retention — plus the auto-i-dat provider-gateway adapters |
| `aftersales` | *(stub)* service orders, appointments, labour, technicians |
| `parts` | *(stub)* part master, stock, suppliers, purchase orders |
| `finance` | *(stub)* invoicing and payment matching. **Not a ledger** — ADR-037 |
| `reporting` | *(stub)* projections from events |
| `compliance` | *(stub)* audit, revDSG, retention |

The stub packages exist so the import-linter contract enumerates every context from day one.
Deliberate — do not delete them, and do not treat a stub as a licence to put its concerns
somewhere else. **WP-5's "open orders" and all of WP-9 depend on two of them.**

**`valuation` is its own standalone bounded context**, not filed under `vehicle` or `sales`.
PRD-Vehicles places it as a sibling of `catalogue`/`vehicle-mdm`/`provider-gateway` — "a dated
commercial opinion, not a vehicle fact," with its own audit and retention rules,
tenant-private even within a group (ADR-029) where vehicle identity itself is global. That
mismatch is why nesting it under `vehicle` would have been wrong, and Sales owning the
trade-in workflow but not the valuation record is why it isn't `sales` either. It is the
**single writer** of every valuation; Sales and Stock hold a `valuationRef` and read the same
record — never a second writer.

**`integration` is likewise its own context.** The Integrations spec draws "one registry,
many gateways": the generic registry (connections/secrets/entitlements/call-log, reusable by
a later `marketplace-gateway`) is a different component from any one provider's gateway
logic. ADR-015 names `provider-gateway` as the likeliest first extraction, on quota
isolation. The auto-i-dat-*specific* adapters live inside `integration` beside the registry
they call — but the **catalogue mirror itself** (the per-tenant variant, option, colour and
tyre content) is `vehicle`'s job, calling `integration.public.call_capability` the same way
`sales` already calls `inventory.public`.

## Rulings you will hit while building

Read the ADR before building against any of these.

**Boundaries and events**

- **ADR-045 — pre-VIN vehicles are pipeline stock items in `inventory`, never records in
  `vehicle-mdm`.** A Sales manual configuration *and* a trade-in on a confirmed contract both
  land in `inventory` as pipeline items. VIN stays mandatory in MDM. Promotion is FR-V-04.
- **ADR-046 — two contract events.** `sales.contract.confirmed` at signature,
  `sales.contract.invoice_requested` at hand-off. Never one name for both moments.
- **ADR-047 — a write spanning two contexts is a call with a compensating action, never a
  shared transaction.** It would work today because everything shares one database. That is
  exactly why it is forbidden. **The import-linter cannot catch this** — it needs its own test.
- **ADR-049 — full commercial visibility inside the dealership** (margin, discounts,
  Wagenbuch), **and ADR-029 unchanged at the group boundary.**
- **ADR-050 — `sales_contract` supersedes the shipped `transaction` table.**
- **ADR-051 — one shared document template layer, built as WP-6b before WP-7.** PDF rendering
  is bought, not built. **WP-6c builds the front-end renderer and calls into it** — one
  template definition, two consumers, never two renderers.
- **ADR-052 — Stock's invoicing gate is a replicated fact, not a synchronous query.**

**Data model**

- **ADR-048 as amended, and ADR-066 — a vehicle carries a LIST of valuations, not one.**
  The newest is current; older ones stay readable as superseded and are never edited or
  deleted. A manual figure is **marked manual everywhere it renders**. A valuation is a
  **standalone application**: creatable with no customer, no offer and no vehicle in the
  register; it carries a **validity period**, and `draft → valid → expired` (plus `used` once
  a contract consumes it) is **derived on read, never stored and never repaired by a nightly
  job**. Sales and Stock are readers.
- **ADR-064 — vehicle-to-customer links carry a role and are time-bounded.** Owner, keeper
  (Halter) and driver are three different parties often enough that collapsing them loses
  information a seller needs. A new holder **closes** the previous row rather than
  overwriting it — including the transfer on `finance.invoice.issued`.
- **ADR-067 — contact channels are child records, not columns.** `customer_phone`,
  `customer_email`, `customer_address`: one row per value, each with a type, an optional
  free-text label, `isPrimary` with **exactly one primary per type-group** enforced
  transactionally, `validFrom`/`validTo`, a `doNotUse` flag with a reason, and **consent per
  channel** with its source and timestamp. The grid keeps `Mobile` / `Email` / `Work phone`
  as **read-model projections**, computed and never stored. **Do not add a flat column** — the
  cheap implementation is exactly what this decision removes.
- **ADR-065 — a credit block stops the contract, not the offer.** Quoting a blocked customer
  is often how the block gets resolved. **Do-not-contact is a different flag** and stops both,
  because it is about contact rather than credit.

**The Configurator (2026-09-03, specified) — substantially built as of 2026-09-13**

> **This section previously said "specified, not yet built." That went stale without
> anyone noticing — audited 2026-09-13 against a pile of miscategorized "In Review"
> Kanban tickets that turned out to already be merged.** Confirmed shipped and tested at
> current `main`: **C-A** (the spec block on all three carriers — `ModelVariant` and
> `VehicleConfiguration` both carry the 38-field `VehicleSpecBlock`, a no-drift
> architecture test enforces it, the three canonical reference lists are seeded), **C-B**
> (catalogue browse + facets, provably making no live provider call, on the shared
> DataGrid), and **C-C** (the `VehicleConfiguration` entity itself, the two-phase overlay,
> both manual and catalogue modes, the never-writes-`vehicle-mdm` architecture guard).
> **C-0** (gateway coverage) is mostly built but **not closed**. PR 1 (the real SOAP
> transport, the seven pre-existing Datennamen), PR 2a (#108, the fourteen new Datennamen
> on the adapter and the mock), PR 2b (#109) and PR 2c (#110) shipped — **all against the
> mock, unverified against a real account**, and most of it has no caller yet: of the
> fourteen, only `FahrzeugArten` is called (by the entitlement probe); the rest wait for C-D
> (KAN-42) or an unbuilt sync leg. **Entitlement (#109):** `gateway.test_connection` probes
> `fahrzeuge` only — the protocol has no "not entitled" signal (p4: an empty string means
> bad credentials *or* Datenname), and only a criteria-free call made right after `System`
> succeeded lets a refusal be attributed to the Datenname. Nothing reads the row it writes,
> so exit criterion 3 stays open. **Code map (#110):** migration `7c4e9a2b6d13` seeds the
> `auto_i_dat` `provider_code_map` (127 rows; identity or strict-subset mappings only —
> every other code becomes a `mapping_gap`, FR-C-10); `112 → engine_cycle`, never
> `drivetrain`, is asserted by test. Exit criterion 4 is **partially met**. The seed is a
> 2021 reading of the spec; whether the provider selects the code group by `FzArt` or
> `FzArtExtern` is unknown (`scripts/verify_auto_i_dat.py` prints the gate), and the sync
> still resolves `Antrieb` as a drivetrain for every kind. Both need a staging auto-i-dat
> account, which doesn't exist yet.
> **C-D** (identification/plate cache) and **C-F** (host integration into the offer flow,
> Stock pipeline and Valuation) were **not** re-verified in that audit (they sit in
> Backlog, not the "In Review" pile that prompted it) — do not assume they're built
> without checking. **C-E (options, packages, colours, wheels/tyres and images,
> KAN-43) shipped 2026-09-14** as three PRs (#93 data layer, #94 Options tab, #95
> Colour/Wheels/Images tab) — grouped options with inline relations, package offers,
> the ADR-072 warn-never-block conflict banner, a catalogue-assisted colour picker
> that never locks out free text, a PneuDimTS tyre-dimension table with `BemDe` shown
> inline, and an entitlement-gated images grid backed by a real `image_url` (see the
> auto-i-dat fact list below — the WP-6 Open Item this closed). Same for **KAN-9
> "Configurator - Standard Flow"** itself: the building blocks below are real, but
> whether the end-to-end flow they compose into is what that ticket actually needs is
> still an open question.

- **ADR-068 — a configuration is a first-class entity** with its own ID, referenced by an
  offer, a stock item, a valuation or a vehicle. **No list screen and no nav entry.**
- **ADR-069 — the configurator is provider-backed.** This **reverses PRD-Sales v2 S-D05**
  ("no provider configurator in v1") and makes WP-6 a hard dependency. Manual configuration
  survives as the off-catalogue path for grey imports, oldtimers, pre-1982 vehicles and
  anything auto-i-dat does not cover.
- **ADR-070 — a configuration never writes `vehicle-mdm`.** It attaches to a pipeline stock
  item, a valuation, or an existing vehicle when a VIN is known. This amends **FR-V-17**,
  which said the ad-hoc valuation creates the vehicle record.
- **ADR-071 — one specification block, three carriers**: the catalogue variant, the
  configuration, and the host's frozen snapshot. A field on one and not the others is a
  defect. `vehicle_model_variant` carries 13 columns today and needs roughly fifty.
- **ADR-072 — option packages, exclusions and extra conditions are stored and shown, never
  enforced.** A conflicting selection warns; it never blocks.

Four facts to carry into that work:

- **auto-i-dat *does* offer VIN decode — corrected by KAN-36, 2026-09-05.** This file,
  PRD-Configurator v1.1 and KAN-9 all previously said the opposite, inferred from the
  *Webservice Fahrzeuge* PDF (which indeed has no VIN-taking Datenname) rather than from the
  provider's actual capability. VIN decode is **DAT-backed**: an auto-i-dat account carries a
  second, independent DAT sub-account (`DAT VIN Account` / `DAT Benutzer` / `DAT Passwort`),
  and only an account with that sub-account populated shows any VIN/VINIdentDB traffic on its
  own billing sheet. Modelled as its own `dat` provider/connection (never folded into the
  `auto_i_dat` connection's config — the two rotate independently); `vin_decode` is a derived
  `integration_entitlement` on the tenant's `auto_i_dat` connection, granted exactly when a
  healthy `dat` connection exists, never hand-declared
  (`app/integration/services/connections.py::compute_vin_decode_entitlement`). **The VIN
  webservice call itself is still not implemented** — no specification for it exists anywhere
  in Drive (the four auto-i-dat PDFs on file are Fahrzeuge, Bewertung, Valuation and Etikette;
  none documents a VIN call), so `AutoIDatSoapAdapter.decode_vin` raises `NotImplementedError`
  until that spec is obtained from auto-i-dat — a procurement step, not an engineering one.
  Stammnummer still resolves only against our own `vehicle-mdm`; plate, Typenschein and
  Werkscode remain the other provider-backed inputs.
- **`vehicle_type_approval` is now a many-to-many with `vehicle_model_variant`** (through
  `vehicle_model_variant_type_approval`); `type_approval_number` is indexed, not unique;
  `first_registration_from` sits on the link. A Typenschein is the number importers use to
  homologate similar vehicles — many variants share one, and one variant carries several.
  *Fixed on branch `fix/type-approval-many-to-many`, migration `eb660a3213bd`; use
  `find_model_variants_by_type_approval` for the 1..n reverse lookup.*
- **`Antrieb` CodeGrpNr 112 is 2-Takt / 4-Takt / Kein Takt** — a stroke count, not a drive
  type. Groups 012 and 022 are Hinten/Vorne/Allrad. It maps to the `engine_cycle` list
  (seeded by C-A), never to `drivetrain`; migration `7c4e9a2b6d13` does exactly that and a
  test asserts it. **`ProviderCodeMap` rows are keyed `(provider_code, raw FzArt, semantic
  code_group = the canonical list's code, raw code)`** — never a numeric CodeGrpNr; the
  model has no such column, and the earlier docstring and PRD wording that said otherwise
  were wrong (the only production reader has always used the semantic name).
- **The full provider image URL is now kept — closed by KAN-43 (C-E), 2026-09-14.**
  `Bilder`'s `BildURL` was being discarded at ingestion: `ImageRef.image_key` (the sync's
  own natural key, `String(160)`) is the URL's basename only, and the full URL had nowhere
  to go — an explicit, unresolved WP-6 Open Item. Without it, no caller could ever render a
  photo, only reference an opaque key. Fixed with a new `ImageRef.image_url` column
  (`String(500)`, migration `90dc1077a569`) threaded through the SOAP adapter, the mock
  adapter, `catalogue_sync`, `catalogue_entitlements` and the OpenAPI schema — not a proxy,
  cache, or base-URL reconstruction scheme, since the provider already returns a complete,
  directly fetchable URL per row.

## Non-negotiable rules

1. **One writer per fact.** Every piece of data has exactly one owning context.
2. **No cross-context foreign keys, joins or shared tables.** Another context's ID is a
   plain `GUID` column with a comment naming the owner, plus a denormalised display label and
   a `labelRefreshedAt` timestamp — the three-column pattern.
3. **No cross-context imports.** Enforced by import-linter, which gates CI and **has no
   suppression mechanism**. `<context>.public` is the only door. If a boundary genuinely needs
   to move, that is an ADR, not an ignore entry. This single rule is what makes the ADR-015
   bet safe.
4. **Every state change others care about is published via the transactional outbox** —
   business row and outbox row in *one local transaction*. Never a dual write.
5. **Events are facts in the past tense, never commands.** `vehicle.odometer.recorded`,
   not `updateVehicle`.
6. **Every consumer is idempotent** by `eventId` against a processed-events table.
   Delivery is at-least-once.
7. **Tenant scope comes from the token, never from a path or body parameter.** Cross-tenant
   reads return **404, never 403** — a 403 confirms the record exists. One narrow exception
   exists for group-scoped reads (ADR-014), implemented as an explicitly enumerated query
   in `app/core`, never as an ambient relaxation of the tenant filter.
8. **Cross-cutting code lives in `app/core`**, written as if it were already an external
   package — no imports back into any bounded context.
9. **Postgres is the test database of record** (ADR-011). SQLite may run as a fast
   pre-commit lane; it may never be the only lane gating a merge.
10. **Distributed integrity is monitored, not assumed.** Nightly reconciliation, dead-letter
    queues with alerting, sync-age alarms — in v1, never "later". *(The reconciliation exists
    and is currently unscheduled — see the WP-5 gaps.)*
11. **No user-facing request may need more than two synchronous hops.** A third hop means a
    projection is missing, not that a call should be added.

## Preserve verbatim — already correct, do not "improve"

- `app/core/tenancy.py` — 404-not-403 scoping, and `get_group_read_or_404`, which authorises
  *before* the row lookup
- `app/core/auth.py::get_current_principal` — tenant from token
- UUIDv7 primary keys (`app/core/uuid7.py`, `app/core/base.py`)
- `version` + `If-Match` optimistic concurrency (`app/core/concurrency.py`)
- POST idempotency keys (`app/core/idempotency.py`)
- Cursor pagination with count threshold (`app/core/pagination.py`)
- The error taxonomy (`app/core/errors.py`) — 400/401/403/404/409/422, one body shape
- `CamelModel` — camelCase JSON over snake_case Python (`app/core/schemas.py`)
- The append-only audit log (`app/core/audit.py`)
- The outbox and consumer harness (`app/core/outbox*.py`, `app/core/consumer.py`,
  `app/core/processed_event_model.py`)
- `EncryptedString` for `tax_id`, and config that refuses to start without the key

## API conventions

Path-versioned `/v1` · camelCase JSON · cursor pagination · `updatedSince` for incremental
sync · `If-Match` on every mutation of a versioned entity · `Idempotency-Key` on every POST
· OpenAPI published per context.

## Swiss domain rules that are easy to get wrong

- **VIN is mandatory** in vehicle MDM. Pre-VIN pipeline vehicles belong to `inventory` and
  are promoted on VIN arrival, idempotently by `pipeline_vehicle_id`.
- **A licence plate is never an identifier.** Wechselschild (one plate, two vehicles),
  reassignment, cantonal changes. `vehicle_plate` is a child table with validity dates and a
  `plate_group_id`, and an ambiguous lookup shows a picker — it never guesses.
- **VAT — ADR-057. There is no `vatTreatment`.** No field, no enum, no column, no badge, no
  selector, no presentation switch — not in the model, not in the API, not in the UI. **Do
  not reintroduce one.** An offer, a contract and an invoice carry **one price: gross, CHF
  incl. MwSt**, after all discounts, with no net line and no VAT breakdown on screen or on
  the customer document. VAT is **one line on the printed document only**, computed at the
  dealer-configurable `dealer_settings.vat_rate`.
  *Why, since this reverses earlier guidance:* Margenbesteuerung was abolished for used cars
  on **1 January 2010** and replaced by the **fiktiver Vorsteuerabzug** (Art. 28a MWSTG). The
  2018 revision reinstated margin taxation only as **Art. 24a MWSTG for Sammlerstücke** —
  first registration more than 30 years before purchase — which is **out of scope for v1**.
  Independent confirmation: AutoScout24's AS24i v34.0 interface has exactly **one** price
  field. ~~ADR-033~~ and ~~ADR-053~~ are superseded.
  **The fiktiver Vorsteuerabzug survives**, but it is a **purchase-side** fact belonging to
  Stock's acquisition booking. Sales never reads it and never writes it.
  *(The single VAT line is specified and NOT yet built — see the WP-8 gaps.)*
- **The surviving confirmation gate is the purchase, not the tax** (Sales S-D10). A contract
  on a stock vehicle cannot be confirmed until that stock item's purchase is booked — the
  dealership would otherwise be selling something it has not acquired.
- **Gapless, immutable document numbering** per legal entity per document type — ADR-032.
  Allocated transactionally, never eventually consistent. Issued documents cannot be
  modified; corrections are credit notes. Closed accounting periods lock against everyone.
  Offer and contract numbers are **working business keys, not the gapless legal number** —
  that is the invoice.
  **[TARGET, WP-9 — none of this exists yet.** `app/sales/services/numbering.py` is offer and
  contract numbering only: per dealership, no document-type dimension, no cancellation record,
  and the number is consumed at flush so a rollback burns it silently. It is **not** the WP-9
  kernel and must not be extended into one without reading ADR-032 first.]
- **Three-level organisation** — group → dealership → location (ADR-014). The **dealership**
  is the tenant. **Customers are group-scoped** (`group_id`, not `tenant_id`). Stock and
  customer history are dealership-owned but readable group-wide; cost, margin, commission,
  discount, trade-in purchase price and valuations stay private to the legal entity.
- **Stock lifecycle and reservation are two independent axes** (ADR-054), never one merged
  status. `lifecycleStatus` (pipeline | in_stock) and `reservationState` (none | reserved)
  are orthogonal; `sold` is not a lifecycle value — an invoiced vehicle has left stock. A
  single enum cannot express "in stock **and** reserved", which is the ordinary case.
- **Group-readable stock is its own enumerated projection** (ADR-055), not the tenant grid
  with columns removed. A test asserts by name that `effectivePrice`, discounts, promotions,
  acquisition and Wagenbuch fields are absent from it.
- **Licensed provider data is tenant-partitioned, never global** (ADR-013). auto-i-dat
  contracts are per dealer, so each dealer's cache is fetched with their own credentials, and
  it must never travel through the cross-tenant shared identity response (FR-V-14).
- **Marketplaces are three, not one** (ADR-062): AutoScout24 (AS24i v34.0, which drives the
  canonical field mapping), Carmarket and Autolina. **Full-delivery semantics** — an object
  no longer transmitted is **deleted** at the marketplace, with its statistics and its URL,
  so unpublish is a confirmed destructive action.
- **i18n: DE, FR, IT, EN** — including reference data. No user-visible string is hardcoded.
  Canonical reference data is translated by us; **provider option text is stored and rendered
  as delivered** and is never translated (ADR-044).

## Working style

- **Plan before code.** Show the plan and wait for approval on anything touching a
  boundary, a migration, or a preserved file.
- **Small PRs.** A large refactor ships as a numbered sequence, each independently
  mergeable with green CI.
- **Never suppress the import-linter.** Raise the boundary question instead.
- **No secrets in the application database** — only references into the secrets manager.
  Secrets are never logged and never returned by any endpoint, to anyone.
- **A work package is not done when the code merges.** The module PRD's status says what
  shipped, the Gap Analysis is regenerated against the new head, and any decision taken
  during the build is an ADR in the Target Architecture. Draft those Notion changes
  alongside the PR, in the same session, or they will not happen.
- **Do not claim an exit criterion in a commit message that the code does not meet.** The
  status table above exists because that happened, across three work packages.
- **`frontend/apps/dms/src/api/types.ts` is generated, not hand-maintained (KAN-35).**
  Every type describing a response or request body is derived from
  `frontend/apps/dms/src/api/schema.d.ts`, which is regenerated from the backend's own
  `app.openapi()` and diffed by the `frontend-openapi-drift` CI job. Hand-editing a
  response/request shape back into `types.ts` is now a mistake the pipeline catches, not a
  quick fix — run `make generate-frontend-types` instead. A shape the backend genuinely
  under-types (a computed field typed `str` where the real value set is a literal union) is
  a `NARROWED` override inside `types.ts`, commented as such, not a reason to hand-write the
  whole interface.
