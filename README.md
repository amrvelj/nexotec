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
- Append-only audit log (`app/models/audit.py`, `app/services/audit.py`)
- Optimistic concurrency via `version` + `If-Match` (`app/core/concurrency.py`)
- POST idempotency-key handling (`app/services/idempotency.py`)
- Cursor pagination (`app/core/pagination.py`)
- Tenant-scoped lookups that 404 (never 403) across tenants (`app/core/tenancy.py`)
- Canonical error taxonomy: 400/401/403/404/409/422 (`app/core/errors.py`)
- Reusable model mixins for future entities (`app/models/base.py`)
- Alembic migrations targeting Postgres

**Issue #2: Dealer + User bootstrap** — the first business entities:

- `Dealer` (`app/models/dealer.py`): the tenant root, Swiss address, canton
  validation, `tax_id` encrypted at rest (`app/models/types.py::EncryptedString`)
- `User` (`app/models/user.py`): tenant-scoped, first FK relationship in the
  schema (`user.tenant_id → dealer.id`), globally-unique email
- `POST /v1/dealers` (platform-admin only), `POST /v1/dealers/{id}/users`,
  full CRUD + audit-log endpoints — see `app/api/v1/dealers.py`
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

Two lanes, both run in CI (`.github/workflows/test.yml`):

**Fast lane — in-memory SQLite, no Docker required:**

```bash
pip install -e ".[dev]"
pytest
```

**Postgres lane — the real container from `docker-compose.yml`.** Required
from issue #2 onward: User→Dealer is the schema's first FK relationship, and
SQLite's weaker constraint/concurrency enforcement can hide bugs (missing FK
violations, isolation differences) that only show up against Postgres.

```bash
docker compose up -d db
DMS_TEST_DATABASE_URL=postgresql+psycopg://dms:dms@localhost:5432/dms_platform pytest
```

Both lanes run the same test suite (`tests/conftest.py` picks the backend
from `DMS_TEST_DATABASE_URL`) — keep SQLite for the fast dev loop, add
Postgres before merging anything with a new FK/constraint.

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

## Out of scope

AUTO-i-DAT, Google Maps, Finance read-API, marketplace ingestion, MOFIS, and
MediaAsset integrations — per the shell scope agreed in `#dms-mdm`.
Customer/Vehicle/Transaction — issues #4 through #6, each an independent PR
against this foundation. Real IdP integration is still unselected —
`app.core.auth.create_access_token` remains a placeholder issuer for
tests/local dev only, not exposed over HTTP; issue #2's `POST /v1/dealers`
+ `POST /v1/dealers/{id}/users` create the tenant/user *records*, not
credentials — see the PR notes for how the first platform_admin token is
minted in the shell. Dealer groups (`parent_group_id`), multi-dealer users,
and fine-grained per-endpoint permissions beyond the five `access_role`
values are explicitly deferred past v1.
