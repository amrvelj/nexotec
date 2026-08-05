# dms-platform

Backend for the Automotive DMS Master Data Management (MDM) shell — Dealer,
User, Customer, Vehicle, and Transaction, built for the Swiss market.

Spec of record: `PLANS/DMS_MDM_V1_SPEC.md` + `PLANS/DMS_MDM_V1_SWISS_ADDENDUM.md`
in the CTO workspace.

## Status

This PR delivers **issue #1: platform foundation only** — no business
entities (Dealer/User/Customer/Vehicle/Transaction) yet. It provides the
infrastructure every later entity is built on:

- FastAPI app, versioned under `/v1`
- Tenant/access-role JWT auth boundary (`app/core/auth.py`)
- UUIDv7 primary keys, monotonic within a process (`app/core/uuid7.py`)
- Append-only audit log (`app/models/audit.py`, `app/services/audit.py`)
- Optimistic concurrency via `version` + `If-Match` (`app/core/concurrency.py`)
- POST idempotency-key handling (`app/services/idempotency.py`)
- Cursor pagination (`app/core/pagination.py`)
- Tenant-scoped lookups that 404 (never 403) across tenants (`app/core/tenancy.py`)
- Canonical error taxonomy: 400/401/403/404/409/422 (`app/core/errors.py`)
- Reusable model mixins for future entities (`app/models/base.py`)
- Alembic migrations targeting Postgres

## Local setup

Requires Python 3.13+ and Docker (for local Postgres).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env   # adjust if needed
docker compose up -d db

alembic upgrade head
uvicorn app.main:app --reload
```

The app reads config from environment variables prefixed `DMS_` (see
`app/core/config.py` and `.env.example`), loaded via `pydantic-settings`.

Verify it's up:

```bash
curl http://localhost:8000/v1/healthz
# {"status":"ok"}
```

## Running tests

Tests run against an in-memory SQLite database, not the Postgres container —
no Docker required for `pytest`. This works because the shell only uses
portable column types (see `app/models/types.py::GUID`); Postgres is still
the target production database via `DMS_DATABASE_URL`, and Alembic migrations
are hand-verified against Postgres DDL (`alembic upgrade head --sql`).

```bash
pip install -e ".[dev]"
pytest
```

## Database migrations

```bash
alembic upgrade head                          # apply
alembic revision -m "add customer table"       # new migration (issue #4+)
alembic upgrade head --sql                     # preview SQL without a live DB
```

## Conventions for entities built on this foundation (issues #2+)

- JSON request/response bodies: camelCase. DB columns: snake_case. FastAPI/
  Pydantic aliasing handles the translation at the API boundary.
- Every entity model inherits `PrimaryKeyMixin` (+ `TenantScopedMixin` unless
  it's explicitly tenant-agnostic like Vehicle, `VersionedMixin`,
  `TimestampMixin`) from `app/models/base.py`.
- Every tenant-scoped lookup goes through `app.core.tenancy.get_or_404` —
  cross-tenant access must return 404, never 403.
- Every mutating endpoint gates on `app.core.auth.require_access_role(...)`.
- Every state-changing write to PII, title/custody, license/tax, or
  transaction-status fields calls `app.services.audit.record_audit_event`.
- List endpoints take `params: PageParams = Depends(page_params)` from
  `app.core.pagination`.
- PATCH/status-transition endpoints take `if_match: int = Depends(require_if_match)`
  from `app.core.concurrency` and call `check_version` before mutating.

## Out of scope for this PR

AUTO-i-DAT, Google Maps, Finance read-API, marketplace ingestion, MOFIS, and
MediaAsset integrations — per the shell scope agreed in `#dms-mdm`. No
business entities (Dealer/User/Customer/Vehicle/Transaction) — those are
issues #2 through #6, each an independent PR against this foundation. No
real IdP integration — `app.core.auth.create_access_token` is a placeholder
issuer for tests/local dev only, not exposed over HTTP; issue #2 (Dealer +
User bootstrap) is expected to define how tokens are actually issued.
