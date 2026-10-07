---
paths:
  - "tests/**"
  - "frontend/**/*.test.ts"
  - "frontend/**/*.test.tsx"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises ADR-011 and the recurring
failure modes from the WP audits (wp-verification). Verified against main@568f416 on
2026-09-28 (every present-tense claim checked against the code); the schema-once bullet
(KAN-133) against its own branch on 2026-10-03. Fix this file in the same PR as any change to
what it states. -->

# Tests

- **Postgres is the lane of record** (ADR-011). SQLite's weaker constraint and isolation
  enforcement hides real bugs; "tested" means Postgres. Every session points `pytest` at this
  checkout's own test database (`dms_test_<checkout>`), so parallel worktrees never share one.
- **On Postgres the schema is built once per session** (`tests/conftest.py`), not per test.
  Every test starts with every table empty (`TRUNCATE`) and without planner statistics. Any DDL
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
  renderer, no password hash and no `credential` table anywhere (to be inverted, not deleted,
  by D-A-07 — see the platform rules), no `vatTreatment`, a non-enumerable plate lookup, the
  shared identity response shape, spec-block carriers that do not drift, enum columns only
  as `StoredEnum` (KAN-86), no foreign key or `relationship()` crossing a context (KAN-84), and
  no shared cross-context transaction: every cross-context call classified, every cross-context
  write committing its own transaction except the one named exception KAN-185 replaces
  (ADR-047, KAN-90).
- **A test that gates an exit criterion must never skip silently.** The one accepted skip is a
  Postgres-only test on the SQLite fast lane (`skipif(not os.environ.get("DMS_TEST_DATABASE_URL"))`,
  as in `test_customer_outbox_idempotency.py`), because CI's gating lane always runs Postgres.
  No other environment-based skip on a gating test.
- **Know what your fixture sets.** A fixture that quietly satisfies a precondition (a
  VAT-registered supplier, a primary contact already present) means the test never exercises
  the case it is named after.
- A new behaviour gets a test that fails without the change. For a new CI guard, prove it goes
  red before it goes green.
- Never change a test to match broken behaviour. A test that disagrees with the spec is a
  question for Anto, not an edit.
- Frontend: render tests opt into jsdom per file (`// @vitest-environment jsdom`) and are named
  `*.render.test.tsx` — not always beside the component: find them with
  `git grep -l <Component> -- '*.render.test.tsx'`. i18n keys are checked by
  `localeKeyParity.test.ts`.
