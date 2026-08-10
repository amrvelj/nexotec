# Nexotec — working rules

Swiss automotive Dealer Management System. FastAPI + Postgres + React SPA (Mantine,
TanStack Query/Table/Virtual, Lucide). One deployable today.

## Commands

- Install backend deps: `pip install -e ".[dev]"`
- Install frontend deps: `npm install --prefix frontend`
- Run backend tests (SQLite, fast lane): `pytest`
- Run backend tests (Postgres lane): `docker compose up -d db && DMS_TEST_DATABASE_URL=postgresql+psycopg://dms:dms@localhost:5432/dms_platform pytest`
- Run a single test: `pytest tests/test_customer.py::test_name`
- Run migrations: `alembic upgrade heads` (plural — one chain per context since PR-3, ADR-015; `head` either fails or silently applies only one context)
- Start the backend: `uvicorn app.main:app --reload`
- Start the frontend: `npm run dev --prefix frontend`
- Lint: `npm run lint --prefix frontend` (oxlint; no backend linter is configured in this repo yet)
- Typecheck: `npm run build --prefix frontend` (runs `tsc -b`; no standalone frontend typecheck script and no backend type checker configured yet)

## Authority

The binding specification lives in Notion. This file is a working summary of it.
**Where this file and Notion disagree, Notion wins — stop and say so.**

- Target Architecture v2.0 — binding, ADR-001 … ADR-038
  https://app.notion.com/p/3b73e79334dd810faf92dddf9268d29b
- Gap Analysis — Target vs. Shipped Code
  https://app.notion.com/p/3b73e79334dd81cbb181f85d58471d52
- Build Sequence v2.0 — the work packages and their exit criteria
  https://app.notion.com/p/3b73e79334dd810496a7f7342ba86f13
- Risk Register
  https://app.notion.com/p/3b73e79334dd8164a502cd06520b7993

Read Notion when this file does not cover the decision in front of you. Do not read it
as a warm-up.

> **The Target Architecture page still contains a "Roadmap to the target" table with
> Stages A–F. That table is SUPERSEDED by ADR-015.** Stages C, D and E describe service
> extractions that are not happening. Ignore them.

## Where we are

**One deployable, one database, and that is deliberate.** ADR-001 makes true microservices
the destination; ADR-015 rules that extraction happens when a trigger fires, not on a
schedule. Right now we are building **hard seams inside one application**.

Do not create a second deployable, a second database, a service template, a broker, or a
`services/` directory. If you think a trigger has fired, say so — do not act on it.

Extraction triggers (any one): a context needs independent scaling · it needs a different
retention or data-residency regime · another context's deploys keep breaking it ·
engineering headcount reaches three · a provider licence demands process isolation.

## Target state vs. shipped

Rules below marked **[TARGET]** are decided but NOT yet implemented. Do not
write code assuming they exist; do not "fix" the discrepancy on your own
initiative. They land in a named work package.

- Customers are group-scoped (`group_id`) — [TARGET, WP-3]. Today `Customer`
  is keyed by `tenant_id` -> `dealer.id` and no `group_id` column exists.
- `vehicle_plate` child table with validity dates — [TARGET, WP-5]. No
  plate-related code exists today.
- `label_en` on reference data — [TARGET, WP-1 PR-6]. Only de/fr/it today.
- import-linter gating CI — [TARGET, WP-1 PR-1]. No config exists today.
- `VehicleParty` belongs to the customer context — [TARGET, WP-1 PR-1].
  Today it is defined in `app/models/vehicle.py`. This is a known existing
  cross-context entanglement and it is PR-1's job to move it.

## The ten bounded contexts

| Context | Owns |
|---|---|
| `platform` | tenants/dealers, users, auth, roles, feature flags, reference data, locations |
| `customer` | customers, contact points, external IDs, duplicates/merges, `VehicleParty` [TARGET] |
| `vehicle` | physical vehicles, catalogue, plates, odometer, custody, provenance |
| `sales` | *(not built)* leads, quotes, orders, contracts, trade-ins, commission |
| `inventory` | *(not built)* stock items, pipeline vehicles, transfers |
| `aftersales` | *(not built)* service orders, appointments, labour, technicians |
| `parts` | *(not built)* part master, stock, suppliers, purchase orders |
| `finance` | *(not built)* invoicing and payment matching. **Not a ledger** — ADR-037 |
| `reporting` | *(not built)* projections from events |
| `compliance` | *(not built)* audit, revDSG, retention |

`reporting` and `compliance` are two separate contexts, not one merged row —
the import-linter contract (once it exists) must enumerate all ten package
roots, not nine.

## Non-negotiable rules

1. **One writer per fact.** Every piece of data has exactly one owning context.
2. **No cross-context foreign keys, joins or shared tables.** Another context's ID is a
   plain `GUID` column with a comment naming the owner.
3. **No cross-context imports.** Enforced by import-linter, which gates CI and **has no
   suppression mechanism**. [TARGET] If a boundary genuinely needs to move, that is an ADR,
   not an ignore entry. This single rule is what makes the ADR-015 bet safe.
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
   package — no imports back into `app/models` or `app/services`.
9. **Postgres is the test database of record** (ADR-011). SQLite may run as a fast
   pre-commit lane; it may never be the only lane gating a merge.
10. **Distributed integrity is monitored, not assumed.** Nightly reconciliation,
    dead-letter queues with alerting, sync-age alarms — in v1, never "later".

## Preserve verbatim — already correct, do not "improve"

- `app/core/tenancy.py` — 404-not-403 scoping, with the reasoning in the docstring
- `app/core/auth.py::get_current_principal` — tenant from token
- UUIDv7 primary keys (`app/core/uuid7.py`, `app/models/base.py`)
- `version` + `If-Match` optimistic concurrency
- POST idempotency keys (`app/services/idempotency.py`)
- Cursor pagination with count threshold (`app/core/pagination.py`)
- The error taxonomy (`app/core/errors.py`) — 400/401/403/404/409/422, one body shape
- `CamelModel` — camelCase JSON over snake_case Python
- The append-only audit log
- `EncryptedString` for `tax_id`, and config that refuses to start without the key

## API conventions

Path-versioned `/v1` · camelCase JSON · cursor pagination · `updatedSince` for incremental
sync · `If-Match` on every mutation of a versioned entity · `Idempotency-Key` on every POST
· OpenAPI published per context.

**No user-facing request may need more than two synchronous hops.** A third hop means a
projection is missing, not that a call should be added.

## Swiss domain rules that are easy to get wrong

- **VIN is mandatory** in vehicle MDM. Pre-VIN pipeline vehicles belong to `inventory` and
  are promoted on VIN arrival, idempotently by `pipeline_vehicle_id`.
- **A licence plate is never an identifier.** Wechselschild (one plate, two vehicles),
  reassignment, cantonal changes. `vehicle_plate` is a child table with validity dates,
  and an ambiguous lookup shows a picker — it never guesses. [TARGET]
- **Margin taxation (Differenzbesteuerung)** — ADR-033. VAT treatment is set at
  *acquisition* and follows the vehicle to sale. A margin-taxed invoice shows **no VAT
  amount**, and margin-taxed vehicle value must stay strictly separated from
  regularly-taxed parts and labour.
- **Gapless, immutable document numbering** per legal entity per document type — ADR-032.
  Allocated transactionally, never eventually consistent. Issued documents cannot be
  modified; corrections are credit notes. Closed accounting periods lock against everyone.
- **Three-level organisation** — group → dealership → location (ADR-014). The **dealership**
  is the tenant. **Customers are group-scoped** (`group_id`, not `tenant_id`). [TARGET] Stock
  and customer history are dealership-owned but readable group-wide; ledger, VAT, cost, margin,
  commission, discount and trade-in purchase price stay private to the legal entity.
- **Licensed provider data is tenant-partitioned, never global** (ADR-013). auto-i-dat
  contracts are per dealer, so each dealer's cache is fetched with their own credentials.
- **i18n: DE, FR, IT, EN** — including reference data [TARGET]. No user-visible string is
  hardcoded.

## Working style

- **Plan before code.** Show the plan and wait for approval on anything touching a
  boundary, a migration, or a preserved file.
- **Small PRs.** A large refactor ships as a numbered sequence, each independently
  mergeable with green CI.
- **Never suppress the import-linter.** Raise the boundary question instead.
- **No secrets in the application database** — only references into the secrets manager.
  Secrets are never logged and never returned by any endpoint, to anyone.
