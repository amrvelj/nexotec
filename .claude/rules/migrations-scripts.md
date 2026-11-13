---
paths:
  - "scripts/migrate_*.py"
  - "scripts/backfill_*.py"
  - "scripts/*migration_smoke_test*.py"
  - "tests/test_backfill_*.py"
---
<!-- Maintainer note. ADR-047, ADR-140. Moved out of migrations.md by KAN-615 (2026-11-13). Last
verified against main@73baf3f (2026-10-25). The post-deploy runs are listed in README.md. -->

# Data-migration scripts and backfills

- **Backfills run for real, on Postgres, and are verified by query**: count rows, count nulls,
  count values identical to the column they were derived from. An offline `--sql` render is not a
  run. A downgrade that is deliberately a no-op says so in a comment.
- **Data-migration scripts** (`scripts/migrate_*.py`, `repoint_*.py`, `backfill_*.py`) are dry-run by
  default (`--commit` writes), idempotent, and reuse the production computations. A historical
  import **publishes no outbox events** (live consumers would act on old history as if new); a
  backfill repairing current state through the production write path publishes what that path
  publishes (`backfill_manual_configuration_reservations.py`). The dry run runs the real path in
  one transaction it rolls back — flushing, so a later row sees an earlier one — refuses any
  commit meanwhile, and reports what the real run would write, including rows it cannot migrate
  and why.
- **No script writes two contexts in one transaction.** The ADR-047 architecture test names the
  migration smoke test's two CI fixtures (`seed_migration_smoke_test.py`,
  `verify_migration_smoke_test_seed.py`), excepted by ADR-140 because they never run against a
  real database; a new one fails by name. Any other script reads a second context through its
  `public` surface.
- A script that must run once per environment after its deploy is listed in README.md («One-time
  backfills after a deploy»).
