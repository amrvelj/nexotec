---
paths:
  - "tests/**"
  - "frontend/**/*.test.ts"
  - "frontend/**/*.test.tsx"
---
<!-- Maintainer note. ADR-011, ADR-086, ADR-089, ADR-126, ADR-130 and the WP audits' failure modes. Loads for every
test, so it holds the invariants only: tests-harness.md, tests-architecture.md and
tests-frontend.md carry the detail (KAN-615). Last verified against main@73baf3f (2026-10-25). -->

# Tests

- **Postgres is the lane of record** (ADR-011); SQLite hides constraint and isolation bugs, so
  "tested" means Postgres. `pytest` uses this checkout's own `dms_test_<checkout>` (each `-n`
  worker its own copy): take the database from the `engine` fixture or `DMS_TEST_DATABASE_URL`,
  never a fixed name, port or path.
- Every test starts with empty tables. **Close every session a test opens** — one left open
  holding a lock fails the test or blocks the next. A test-only model sits on `DemoBase`
  (`tests/demo_models.py`), never `app.db.Base`. Harness detail: `tests-harness.md`.
- **Signing in:** a user created through the API is `invited` and holds no password;
  `tests/identity_support.py` reads the invitation out of the `mailbox` fixture (the recording
  platform sender), accepts it and signs in with `POST /v1/auth/login` — `PasswordLogin` for a
  user signed in more than once. A token from `create_access_token` without `session_id`
  bypasses the session guard; one with a `sid` must name a live row.
- The suite never asks Have I Been Pwned (autouse fixture); a test about the breach check turns
  `hibp_enabled` on and patches `password_policy.hibp_breach_count`.
- **Architecture tests (`tests/architecture/`) are rulings in executable form.** Never weaken,
  skip or loosen one to make a change pass; if one blocks you, the change or the ruling is
  wrong — raise it (`tests-architecture.md`).
- **A gating test never skips silently.** The one accepted skip is a Postgres-only test on the
  SQLite fast lane (`skipif(not os.environ.get("DMS_TEST_DATABASE_URL"))`, or a reasoned marker
  of `tests/postgres_only.py`) — CI's gating lane always runs Postgres. No other
  environment-based skip on a gating test.
- **Know what your fixture sets.** A fixture that quietly satisfies a precondition (a
  VAT-registered supplier, a primary contact already present) means the test never exercises
  the case it is named after.
- A new behaviour gets a test that fails without the change; a new CI guard is proved red before
  it goes green.
- Never change a test to match broken behaviour. A test that disagrees with the spec is never
  edited to pass: a business point (what a user sees, money, legal, a ruling of Anto's) is a
  question for Anto; a technical one is decided and recorded as an ADR (ADR-126).
- Frontend tests (jsdom, the fake backend, the test theme): `tests-frontend.md`.
