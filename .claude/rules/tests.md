---
paths:
  - "tests/**"
  - "frontend/**/*.test.ts"
  - "frontend/**/*.test.tsx"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises ADR-011 and the recurring
failure modes from the WP audits (wp-verification). Verified against main@7a90ae4 on
2026-10-12 (every present-tense claim checked against the code). Fix this file in the same PR
as any change to what it states. -->

# Tests

- **Postgres is the lane of record** (ADR-011). SQLite's weaker constraint and isolation
  enforcement hides real bugs; "tested" means Postgres. Every session points `pytest` at this
  checkout's own test database (`dms_test_<checkout>`), so parallel worktrees never share one.
- **On Postgres the schema is built once per session** (`tests/conftest.py`), not per test.
  Every test starts with every table empty (`TRUNCATE`) and without planner statistics, except
  the `country` reference list the autouse `_seed_country_reference_list` fixture seeds. Any DDL
  a test runs (an event trigger counts it), or an `ANALYZE`/`VACUUM` of a test table, has the
  schema rebuilt before the next test. A session from the `engine` fixture that a test leaves
  open in a transaction, holding a lock on a test table, fails that test at its teardown and is
  terminated so the run goes on; a connection opened any other way is not ended and can block
  the next test until `lock timeout`. Close every session a test opens. The test role must be a
  superuser, as CI, docker compose and `scripts/dev/cloud-postgres` make it.
  `tests/test_postgres_test_isolation.py` pins the reset, the rebuild and the session check.
- **Architecture tests (`tests/architecture/`) are rulings in executable form.** Never weaken,
  skip or loosen one to make a change pass. If one blocks you, either the change or the ruling
  is wrong — raise it. They guard: gateway-only auto-i-dat calls, the capability vocabulary,
  catalogue browse without provider calls, configurations never writing vehicle-mdm, margin
  never in a rendered document, no ambient group reads, layout code only in the document
  renderer, password-shaped columns in `credential` and nowhere else and no second credential
  table outside the allow-list (inverted by KAN-196 as D-A-07 ruled; a third realm is an ADR),
  no `vatTreatment`, a non-enumerable plate lookup, the
  shared identity response shape, spec-block carriers that do not drift, enum columns only
  as `StoredEnum` (KAN-86), no foreign key or `relationship()` crossing a context (KAN-84),
  no shared cross-context transaction: every cross-context call classified, every cross-context
  write committing its own transaction except the two allow-listed entries, the KAN-185 debt
  (tracked, not ruled) and `scripts/migrate_transaction_rows.py` under ADR-091, the one
  exception ruled by ADR (KAN-106; the test scans `scripts/` for that and records every other
  multi-context script by name) (ADR-047, KAN-90), every POST an `IdempotentRoute` declaring an optional
  `Idempotency-Key`, the nine ratified exemptions aside — logout, switch-dealership and the seven
  unauthenticated front-door POSTs (KAN-196); a new one is Anto's ruling by name —
  every route of a sales, inventory, valuation or configurator router behind the `sales`
  entitlement gate and no other route (KAN-198), every production token minted with an explicit
  entitlement set (KAN-198),
  no standard-text write behind the plain `require_write` gate instead of the delegation-aware one,
  and that gate only on a route whose path carries `{category}` (KAN-199),
  every internal idempotency key in the reserved `<context>.<action>:` grammar clients are
  refused (KAN-264), every `created_by`/`updated_by` column reconciled against `user.id`
  (KAN-243), and the customer-group predicate used only by reconciliation (KAN-259).
- **A test that gates an exit criterion must never skip silently.** The one accepted skip is a
  Postgres-only test on the SQLite fast lane (`skipif(not os.environ.get("DMS_TEST_DATABASE_URL"))`,
  as in `test_customer_outbox_idempotency.py`), because CI's gating lane always runs Postgres.
  No other environment-based skip on a gating test.
- **The identity front door in tests (KAN-196):** a user created through the API is `invited`
  and holds no password; `tests/identity_support.py` reads the invitation out of the `mailbox`
  fixture (the recording platform sender), accepts it and signs in with `POST /v1/auth/login`
  — `PasswordLogin` for a user signed in more than once. A token from `create_access_token`
  without `session_id` bypasses the session guard; one with a `sid` must name a live row. The
  suite never asks Have I Been Pwned (autouse fixture); a test about the breach check turns
  `hibp_enabled` on and patches `password_policy.hibp_breach_count`.
- **Know what your fixture sets.** A fixture that quietly satisfies a precondition (a
  VAT-registered supplier, a primary contact already present) means the test never exercises
  the case it is named after.
- A new behaviour gets a test that fails without the change. For a new CI guard, prove it goes
  red before it goes green.
- Never change a test to match broken behaviour. A test that disagrees with the spec is a
  question for Anto, not an edit.
- Frontend: render tests opt into jsdom per file (`// @vitest-environment jsdom`) and are named
  `*.render.test.tsx` — not always beside the component: find them with
  `git grep -l <Component> -- '*.render.test.tsx'`. In `frontend/apps/dms` a jsdom test never
  reaches the network: its default `fetch` is a stand-in that never settles, and the test fails
  naming the request unless it calls `installFakeBackend` (`src/test/fakeBackend.ts`; KAN-172).
  i18n keys are checked by `localeKeyParity.test.ts`.
