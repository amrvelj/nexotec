# dms-platform

Backend for the Automotive DMS Master Data Management (MDM) shell — Dealer,
User, Customer, Vehicle, and Transaction, built for the Swiss market.

Spec of record: `PLANS/DMS_MDM_V1_SPEC.md` + `PLANS/DMS_MDM_V1_SWISS_ADDENDUM.md`
in the CTO workspace.

## Status

**Issue #1: platform foundation** — infrastructure every entity is built on:

- FastAPI app, versioned under `/v1`
- Tenant/access-role JWT auth boundary (`app/core/auth.py`)
- UUIDv7 primary keys, monotonic within a process (`app/core/uuid7.py`)
- Append-only audit log (`app/core/audit_model.py`, `app/core/audit.py`)
- Optimistic concurrency via `version` + `If-Match` (`app/core/concurrency.py`)
- POST idempotency-key handling (`app/core/idempotency.py`)
- Cursor pagination (`app/core/pagination.py`)
- Tenant-scoped lookups that 404 (never 403) across tenants (`app/core/tenancy.py`)
- Canonical error taxonomy: 400/401/403/404/409/422 (`app/core/errors.py`)
- Reusable model mixins for future entities (`app/core/base.py`)
- Alembic migrations targeting Postgres

**Issue #2: Dealer + User bootstrap** — the first business entities:

- `Dealer` (`app/platform/models/dealer.py`): the tenant root, Swiss address,
  canton validation, `tax_id` encrypted at rest (`app/core/types.py::EncryptedString`)
- `User` (`app/platform/models/user.py`): tenant-scoped, first FK relationship
  in the schema (`user.tenant_id → dealer.id`), globally-unique email
- `POST /v1/dealers` (platform-admin only), `POST /v1/dealers/{id}/users`,
  full CRUD + audit-log endpoints — see `app/platform/api/dealers.py`
- License/tax_id/status (Dealer) and role/access_role/status (User) changes
  are audit-logged; `offboarded` and `terminated` are terminal states
- Postgres CI test lane (see "Running tests") — CTO's merge condition, since
  this is the first PR with a real FK to verify against Postgres, not just
  SQLite's more permissive constraint enforcement

## Local setup

Requires Python 3.13+ and Docker (for local Postgres).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env   # adjust if needed
docker compose up -d db

alembic upgrade heads
uvicorn app.main:app --reload
```

The app reads config from environment variables prefixed `DMS_` (see
`app/core/config.py` and `.env.example`), loaded via `pydantic-settings`.

Verify it's up:

```bash
curl http://localhost:8000/v1/healthz
# {"status":"ok"}
curl http://localhost:8000/v1/readyz
# {"status":"ready"} — or 503 {"status":"not_ready", ...} if the database isn't reachable
```

## Running the whole stack (WP-2 PR-3)

One command brings up Postgres, the API, and the outbox worker together,
seeded, on a clean machine — no local Python/Node install required:

```bash
make up
```

First run generates a local-only `.env` with fresh dev secrets
(`DMS_TAX_ID_ENCRYPTION_KEY`, `DMS_JWT_PRIVATE_KEY`) if one doesn't already
exist — see the Makefile's own comment for why. `make down` stops it,
`make logs` tails all three services. This is `docker-compose.yml`'s `app`
and `worker` services — same image (`Dockerfile`), different entrypoint;
not a second deployable (ADR-001/ADR-015), mirroring `render.yaml`'s own
web/worker split.

## Observability (WP-2 PR-3, closes G-16)

Structured JSON logs with `correlationId`/`tenantId` on every line always
apply — no external service required. `X-Correlation-Id` is accepted on
the way in and echoed back on the way out; a request without one gets a
generated one. `GET /v1/readyz` checks the database is actually reachable
(`GET /v1/healthz` deliberately stays a static liveness reply — see its own
docstring for why touching the database there would be the wrong design).

Tracing, metrics and Sentry error reporting are entirely optional and
genuinely inert until configured — see `.env.example`'s commented-out
`DMS_OTEL_*`/`DMS_SENTRY_DSN` vars. Point `DMS_OTEL_EXPORTER_OTLP_ENDPOINT`
at Grafana Cloud's OTLP gateway to get distributed traces (via
`opentelemetry-instrumentation-fastapi`, auto-instrumented) and RED metrics
per endpoint for free; `app.worker`'s heartbeat additionally emits three
alarm gauges every 30s once metrics are configured:

| Metric | What it means | Suggested alert |
| --- | --- | --- |
| `dms.outbox.lag_seconds` | Age of the oldest still-pending outbox message | fires if > 300s |
| `dms.outbox.dead_letter_count` | Messages that exhausted all retry attempts | fires if > 0 |
| `dms.consumer.lag_seconds{consumer_name=...}` | Time since a consumer last processed anything | fires if > 300s, per consumer |

No real consumer is registered yet (PR-4 shipped outbox machinery only, no
business events — see `app/worker.py::register_handlers`), so
`dms.consumer.lag_seconds` has nothing to report in production today; the
metric and its alert are ready for the first one that lands. Configure the
actual Grafana alert rules in your Grafana Cloud instance against these
metric names — that's account-specific and isn't something a config file
in this repo can provision for you.

## Running tests

**Postgres is the only lane that gates a merge** (ADR-011,
`.github/workflows/test.yml`'s `postgres` job) — the real container from
`docker-compose.yml`. SQLite's weaker constraint/concurrency enforcement
can hide bugs (missing FK violations, isolation differences) that only
show up against Postgres.

```bash
docker compose up -d db
DMS_TEST_DATABASE_URL=postgresql+psycopg://dms:dms@localhost:5432/dms_platform pytest
```

**SQLite is a fast, optional, local-only convenience** (ADR-011) — not a
CI lane at all as of WP-2 PR-4, only a `pre-commit` hook you can opt into:

```bash
pip install pre-commit
pre-commit install
```

That's `pytest` with no `DMS_TEST_DATABASE_URL` set — same test suite
either way (`tests/conftest.py` picks the backend from that env var), just
faster and with no Docker required. Add the Postgres lane locally too
before pushing anything with a new FK/constraint — pre-commit running
green is not the same guarantee CI's `postgres` job gives you.

## Database migrations

One chain per bounded context (PR-3, ADR-015), branched forward from a
frozen shared trunk — `alembic heads` lists all of them. Always use
`heads` (plural), never `head`: with multiple independent chains, the
singular form either fails or silently applies only one.

```bash
alembic upgrade heads                                              # apply every context's head
alembic heads                                                      # list current heads, one per context
alembic revision --head customer@head -m "add loyalty tier"        # new migration, targets one context's chain
alembic upgrade heads --sql                                        # preview SQL without a live DB
```

`--head <context>@head` matters for new revisions: without it, Alembic
doesn't know which chain (i.e. which `alembic/versions/<context>/`
directory) a new migration belongs to. A migration in one context's chain
must never touch another context's tables — that's the whole point of the
split; cross-context schema changes predate the split and stay in the
frozen trunk, they don't get new ones.

Downgrading needs the same care: `alembic downgrade -1` is undefined once
there's more than one head (one step back from *which* head?). Target an
explicit revision instead, e.g. `alembic downgrade <revision-id>`, or
scope to one context's own chain.

## Conventions for entities built on this foundation (issues #2+)

- JSON request/response bodies: camelCase. DB columns: snake_case. FastAPI/
  Pydantic aliasing handles the translation at the API boundary.
- Every entity model inherits `PrimaryKeyMixin` (+ `TenantScopedMixin` unless
  it's explicitly tenant-agnostic like Vehicle, `VersionedMixin`,
  `TimestampMixin`) from `app/core/base.py`.
- Every tenant-scoped lookup goes through `app.core.tenancy.get_or_404` —
  cross-tenant access must return 404, never 403.
- Every mutating endpoint that used to read a module's own `_WRITE_ROLES`
  tuple now gates on `app.core.permissions.require_write("<capability>")`
  (or `require_read` for a tightened-from-"any role" read) — see that
  module's `CAPABILITY_MATRIX` for what's defined. `app.core.auth.
  require_access_role(...)` is still the right tool for a plain role-
  membership gate that isn't in the Notion permission matrix (the
  platform_admin-only endpoints).
- Every state-changing write to PII, title/custody, license/tax, or
  transaction-status fields calls `app.core.audit.record_audit_event`.
- List endpoints take `params: PageParams = Depends(page_params)` from
  `app.core.pagination`.
- PATCH/status-transition endpoints take `if_match: int = Depends(require_if_match)`
  from `app.core.concurrency` and call `check_version` before mutating.

## Out of scope

AUTO-i-DAT, Google Maps, Finance read-API, marketplace ingestion, MOFIS, and
MediaAsset integrations — per the shell scope agreed in `#dms-mdm`.
Customer/Vehicle/Transaction — issues #4 through #6, each an independent PR
against this foundation. Real IdP integration is still unselected —
`app.core.auth.create_access_token` remains a placeholder issuer for
tests/local dev only, not exposed over HTTP; issue #2's `POST /v1/dealers`
+ `POST /v1/dealers/{id}/users` create the tenant/user *records*, not
credentials — see the PR notes for how the first platform_admin token is
minted in the shell. Dealer groups (`parent_group_id`) and multi-dealer
users are still explicitly deferred past v1; per-capability permissions
(WP-2 PR-2) landed — see `app.core.permissions.CAPABILITY_MATRIX`.
