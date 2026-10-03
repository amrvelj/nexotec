# Nexotec — working rules

Swiss automotive Dealer Management System. FastAPI + Postgres + React SPA (Mantine,
TanStack Query/Table/Virtual, Lucide). One deployable today.

**Quality before speed.** This product is meant to last. Where a shortcut and a correct
implementation disagree, take the correct one and say what it costs. Do not cut scope, skip
a migration, or lower a bar to protect a date — no date in this project outranks getting it
right.

## What this file is

- Rules that are true in **every** session, whatever you are working on. Rulings for one area
  live in `.claude/rules/` and load when you read a file in that area. Workflows are skills
  (`/ticket`, `/verify`, `/spec-review`, `/architect`, `/drift-audit`). Enforcement is hooks,
  and they block — `.claude/README.md` explains each gate.
- **No build status.** What is built is in the code; what is planned or open is in Notion
  (Build Sequence, Gap Analysis, Kanban). Status tables have gone stale everywhere, Notion's
  included — check the code before relying on any "built" / "not built" claim.
- These files are summaries, and summaries go stale: before building against a ruling they
  cite, read the ADR or PRD section behind it. There is exactly one CLAUDE.md, at the root.

## Authority

Notion is binding; this file and the rules summarise it. **Where they disagree, Notion wins —
stop and say so.** If the disagreement is in what your change touches, correct the file in the
same PR (the reviewer checks); otherwise file a Kanban ticket with the replacement text.

- Target Architecture — binding; its ADR log is the authority on decisions
  https://app.notion.com/p/3b73e79334dd810faf92dddf9268d29b
- UI/UX Specification — the one UI page; wins over a module PRD on presentation
  https://app.notion.com/p/3b53e79334dd81b2a18ed1540054d175
- Build Sequence https://app.notion.com/p/3b73e79334dd810496a7f7342ba86f13 ·
  Gap Analysis https://app.notion.com/p/3b73e79334dd81cbb181f85d58471d52 ·
  Risk Register https://app.notion.com/p/3b73e79334dd8164a502cd06520b7993
- Kanban board — every change has a ticket https://app.notion.com/p/3cf3e79334dd80f69bb8c2e6a19481ef
- PRDs: `Customers` https://app.notion.com/p/3b53e79334dd80c4a7e2e92fc3a85986 · `Vehicles` https://app.notion.com/p/3b73e79334dd81028e76f3bdd24beeff
  · `Stock` https://app.notion.com/p/3bb3e79334dd8073be48d91997001fae · `Sales v2` https://app.notion.com/p/3bb3e79334dd80cd89eee23899f031e6
  · `Configurator` https://app.notion.com/p/3cf3e79334dd80c4af05ddb042da7d9d · `Dealer Administration` https://app.notion.com/p/3e13e79334dd815c8c87ea2467c0e6bc
  · `Authentication & Identity` https://app.notion.com/p/3e13e79334dd81e5ab31fe5d8b2ba8bd
  · `Integrations & API Credentials` https://app.notion.com/p/3b73e79334dd8122bea0daa6b2b92df8

Otherwise read Notion when these files do not cover the decision in front of you — not as a
warm-up. The Target Architecture's "Roadmap to the target" table (Stages A–F) is superseded
by ADR-015; ignore it.

## Commands

- Once per checkout (main checkout, worktree, every cloud session): `scripts/dev/bootstrap` — its own `.venv`,
  `npm ci`, its own test database (in a worktree, a dev database copied from the main one; in a
  cloud session, it first starts that machine's Postgres). The session already has this checkout's `.venv/bin` first on PATH and `pytest` pointed
  at its test database, so the tools work as soon as bootstrap finishes. Never call another
  checkout's `.venv`: its editable install runs that checkout's code.
- The check the push gate trusts: `scripts/dev/check` (lanes chosen from the diff;
  `--full` runs all; `--fast` is the SQLite lane and records nothing)
- Tests: `pytest` — Postgres, the lane of record. One test: `pytest tests/test_customer.py::test_name`
- Migrations: `alembic upgrade heads` — plural: one chain per context (ADR-015); `head`
  fails or silently applies one context only
- Backend: `ruff check app tests` · `python -m mypy app` (as a module — the bare `mypy`
  binary resolves another interpreter) · `lint-imports`
- Frontend: `npm run lint --prefix frontend` · `npm run build --prefix frontend` ·
  `npx vitest run` in `frontend/apps/dms` or `frontend/packages/ui-kit`
- API types: `make generate-frontend-types` — `schema.d.ts` is generated, never edited
- Run it: the desktop preview (`.claude/launch.json`: api, frontend, prototype; stop Docker's
  `app` first — both want port 8000), or `make up` for the whole stack in Docker
- Gates: `scripts/dev/gate status` shows what the push and the hand-over still need

CI (`.github/workflows/test.yml`) runs eleven jobs; whether they block a merge is `main`'s branch protection.

## How we work

- Ticket work runs through `/ticket KAN-n` — also when Anto asks for a ticket in words. Work
  without a ticket is a spike: say so, and push it only when Anto asks (it gets a ticket then).
- **Plan before code.** Show the plan and wait for approval when a change touches a context
  boundary, a migration, a preserved file, or a public API contract.
- Small PRs. A large change ships as a numbered sequence, each mergeable with green CI.
- Never suppress the import-linter; raise the boundary question instead.
- A decision taken while building is an ADR in the Target Architecture, not a PR comment.
  A defect or open question found while building gets its own Kanban ticket, in that session.
- **Work is not done when the code merges:** the module PRD's status says what shipped and the
  Gap Analysis cites the new head — every time, whether or not the ticket names them; draft those edits in the same session.
- **Before a Notion write that asks Anto**, say in one plain sentence which page changes and how: his prompt shows only raw JSON.
- Commit messages and PR descriptions are claims. Never round a partial up: say which exit
  criteria are met and which are **not**, and what you did not verify.
- "Verified" means a check you ran, on this code — and a screenshot: the changed screen; for a
  fix, the reproduction that no longer reproduces; for work with no screen of its own, the
  nearest visible artefact. "None exists" is said in one line, never assumed. The hand-over
  gate enforces both.
- Code work happens in a worktree or a cloud session, each on its own copy. The main checkout stays on `main`, clean.
- No secrets in the application database, only references into the secrets manager. Secrets
  are never logged and never returned by any endpoint, to anyone.

## Where we are

**One deployable, one database — deliberately.** ADR-001 makes microservices the destination;
ADR-015 rules that extraction happens when a trigger fires, not on a schedule: a context needs
independent scaling · a different retention or data-residency regime · another context's deploys
keep breaking it · engineering headcount reaches three · a provider licence demands process
isolation. Until then we build **hard seams inside one application** — no second deployable,
second database, service template, broker or `services/` directory. A trigger fired? Say so; do not act.

## The twelve bounded contexts

| Context | Owns |
|---|---|
| `platform` | tenants/dealers, users, auth, roles, feature flags, reference data, locations, document templates |
| `customer` | customers (**group-scoped**, ADR-014), contact channels as child collections (ADR-067), external IDs, duplicates/merges, `VehicleParty` |
| `vehicle` | physical vehicles, catalogue (canonical taxonomy + per-tenant provider mirror), plates, odometer, custody, provenance, the configurator |
| `sales` | offers and contracts, pricing build-up, trade-ins, the offer workspace, documents |
| `inventory` | stock items, pipeline vehicles, reservation, Wagenbuch, marketplace publishing |
| `valuation` | the standalone valuation application (ADR-066), tenant-private (ADR-029) |
| `integration` | the integration registry (connections, write-only secret refs, entitlements, call log, retention) + provider-gateway adapters |
| `aftersales` | *(stub)* service orders, appointments, labour, technicians |
| `parts` | *(stub)* part master, stock, suppliers, purchase orders |
| `finance` | *(stub)* invoicing and payment matching — **not a ledger** (ADR-037) |
| `reporting` | *(stub)* projections from events |
| `compliance` | *(stub)* audit, revDSG, retention |

The stubs exist so the import-linter contract enumerates every context from day one. Do not
delete them, and do not treat a stub as a licence to put its concerns somewhere else.

## Non-negotiable rules

1. **One writer per fact.** Every piece of data has exactly one owning context.
2. **No cross-context foreign keys, joins or shared tables.** Another context's ID is a plain
   `GUID` column with a comment naming the owner, plus a denormalised display label and a
   `labelRefreshedAt` timestamp — the three-column pattern.
3. **No cross-context imports.** Enforced by import-linter, which gates CI and **has no
   suppression mechanism**. `<context>.public` is the only door. Moving a boundary is an ADR,
   not an ignore entry. This rule is what makes the ADR-015 bet safe.
4. **Every state change others care about goes through the transactional outbox** — business
   row and outbox row in *one local transaction*. Never a dual write.
5. **Events are facts in the past tense, never commands.** `vehicle.odometer.recorded`, not
   `updateVehicle`.
6. **Every consumer is idempotent** by `eventId` against a processed-events table. Delivery is
   at-least-once.
7. **Tenant scope comes from the token, never from a path or body parameter.** Cross-tenant
   reads return **404, never 403** — a 403 confirms the record exists. The one exception,
   group-scoped reads (ADR-014), lives only in the files `tests/architecture/test_no_ambient_group_read.py`
   allow-lists (`get_group_read_or_404` in `app/core/tenancy.py` among them); a new one is an
   ADR plus an allowlist entry, never an ambient relaxation of the tenant filter.
8. **Cross-cutting code lives in `app/core`**, written as if it were already an external
   package — no imports back into any bounded context.
9. **Postgres is the test database of record** (ADR-011). SQLite may run as a fast local lane;
   it never counts as verification and never gates a merge.
10. **Distributed integrity is monitored, not assumed.** Nightly reconciliation, dead-letter
    queues with alerting, sync-age alarms — in v1, never "later".
11. **No user-facing request needs more than two synchronous hops.** A third hop means a
    projection is missing, not that a call should be added.
12. **A write spanning two contexts is a call with a compensating action, never a shared
    transaction** (ADR-047) — each side commits its own work. It would work today because
    everything shares one database; that is why it is forbidden. The import-linter cannot
    catch it, so it needs its own test.

## Preserve verbatim — already correct, do not "improve"

Edits to these files (all in `app/core/`) ask Anto first, whichever tool makes them.

- `tenancy.py` — 404-not-403 scoping, and `get_group_read_or_404`, which authorises *before*
  the row lookup · `auth.py::get_current_principal` — tenant from token
- UUIDv7 primary keys (`uuid7.py`, `base.py`) · `version` + `If-Match` optimistic concurrency
  (`concurrency.py`) · POST idempotency keys (`idempotency.py`) · cursor pagination with count
  threshold (`pagination.py`)
- The error taxonomy (`errors.py`) — 400/401/403/404/409/422, one body shape · `CamelModel`,
  camelCase JSON over snake_case Python (`schemas.py`) · the append-only audit log (`audit.py`)
- The outbox and consumer harness (`outbox*.py`, `consumer.py`, `processed_event_model.py`)
- `EncryptedString` for `tax_id` (`types.py`), and config that refuses to start without the key
  (`config.py`)

## API conventions

Path-versioned `/v1` · camelCase JSON · cursor pagination · `updatedSince` for incremental
sync · `If-Match` on every mutation of a versioned entity · `Idempotency-Key` on every POST ·
OpenAPI published per context. A newly required request field is a breaking contract change:
it needs an explicit decision, never a side effect of a fix.

## Domain rules that apply everywhere

- **Organisation** (ADR-014): group → dealership → location. The **dealership** is the tenant.
  Customers are group-scoped (`group_id`); stock and customer history are dealership-owned
  but readable group-wide. Cost, margin, commission, discount, trade-in purchase price and
  valuations stay private to the legal entity. All writes require switching the active
  dealership.
- **One administration surface (A-RULE-0):** every setting a dealership owns is configured in
  Dealer Administration. No module ships its own settings screen.
- **Vehicle identity:** VIN is mandatory in vehicle-mdm; a licence plate is never an
  identifier (details in `.claude/rules/vehicle.md`).
- **VAT** (ADR-057): one price — gross, CHF incl. MwSt; no net line or VAT breakdown on
  screen, VAT is one line on the printed document only. **There is no `vatTreatment`** — no
  field, enum, column, badge or switch, anywhere; `tests/architecture/test_no_vat_treatment_field.py`
  enforces it. Details in the sales and inventory rules.
- **i18n:** DE, FR, IT, EN are all first class, reference data included. No user-visible
  string is hardcoded; a missing key renders a loud marker, never a German fallback; the
  customer's correspondence language is not the user's UI language.
- **Licensed provider data** (auto-i-dat) is tenant-partitioned, never global (ADR-013).

## Reference material outside the repository

The Nexotec Drive folder — quote it: `"/Users/antomrvelj/Library/CloudStorage/GoogleDrive-mrvelj.anto@gmail.com/Meine Ablage/1. Persönlich/11. Arbeit/Anto/Nexotec"`:
the UI prototype (`.claude/rules/ui.md`), auto-i-dat PDFs, dated reports. Opened only when a task names it; a cloud session cannot reach it — say so instead of guessing.
