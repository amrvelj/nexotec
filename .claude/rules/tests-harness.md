---
paths:
  - "tests/conftest.py"
  - "tests/*_support.py"
  - "tests/demo_models.py"
  - "tests/postgres_only.py"
  - "tests/checkout_guard.py"
  - "tests/test_postgres_test_isolation.py"
  - "tests/test_demo_models_off_production_base.py"
---
<!-- Maintainer note. ADR-011, ADR-130 (per-worker databases). Moved out of tests.md by KAN-615
(2026-11-13). Last verified against main@32ad509 (2026-11-25). -->

# The Postgres test harness

- **The schema is built once per session** (`tests/conftest.py`), not per test. Every test starts
  with every table empty (`TRUNCATE`) and without planner statistics, except what two autouse
  fixtures seed: the `country` reference list (`_seed_country_reference_list`) and the
  notification catalogue, `notification_event` (`_seed_notification_catalogue`, KAN-216). Any DDL a test runs
  (an event trigger counts it), or an `ANALYZE`/`VACUUM` of a test table, has the schema rebuilt
  before the next test.
- A session from the `engine` fixture left open in a transaction holding a lock on a test table
  fails that test at its teardown and is terminated so the run goes on; a connection opened any
  other way is not ended and can block the next test until `lock timeout`. The test role must be
  a superuser, as CI, docker compose and `scripts/dev/cloud-postgres` make it.
  `tests/test_postgres_test_isolation.py` pins the reset, the rebuild and the session check.
- `Base.metadata` holds production tables only: test-only models sit on `DemoBase`; conftest
  builds and resets both (`TEST_METADATA`; `tests/test_demo_models_off_production_base.py`).
- **`pytest -n auto` gives every worker its own database** (`<name>_gw0`, `_gw1` …): conftest
  creates it (dropping first one a killed run left behind, KAN-773), builds the schema as for the single one and drops it at the end (ADR-130).
- **A run refuses another checkout** (KAN-783, `tests/checkout_guard.py`): `pytest_configure` exits
  (code 4) when `sys.prefix` is another checkout's `.venv`, or `DMS_TEST_DATABASE_URL` names another
  checkout's `dms_test_<slug>` — only the own name, its `_gw<n>` and the suite's listed scratch suffixes pass (KAN-802,
  `_DERIVED`; a new derived name is added there), and git failing to say worktree-or-not refuses; names outside that
  scheme (CI's) are not judged.
