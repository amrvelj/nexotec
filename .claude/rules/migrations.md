---
paths:
  - "alembic/**"
  - "alembic.ini"
  - "app/**/models/**"
  - "app/core/*_model.py"
  - "app/model_registry.py"
  - "scripts/migrate_*.py"
  - "scripts/repoint_*.py"
  - "scripts/backfill_*.py"
  - "app/core/enum_type.py"
  - "scripts/*migration_smoke_test*.py"
  - "app/db.py"
  - "tests/test_migrated_*.py"
  - "scripts/check_concurrent_alembic_upgrade.py"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises ADR-015 (PR-3 chains),
ADR-011, ADR-021, ADR-088, rule 2 and the verification lessons from the WP audits; also cites
ADR-047, ADR-091. Verified against main@73baf3f on 2026-10-25 (every present-tense claim checked
against the code). Fix this file
in the same PR as any change to what it states. -->

# Schema, migrations and data migrations

- **`alembic upgrade heads` — plural.** There is one chain per context under
  `alembic/versions/<context>/`, each rooted in a branch-root revision labelled with the context
  and branched from the frozen shared trunk (`b36486886126`: the 19 pre-split revisions in
  `alembic/versions/` itself, which never move) (ADR-015, PR-3). New changes to
  `app/core/*_model.py` tables go in the `core` chain, though `audit_event`,
  `idempotency_record` and the reconciliation tables were created in the trunk. A new migration
  descends from its own context's branch — `alembic revision -m "…" --head customer@head` (with
  `--autogenerate` when models changed) — which places the file in that context's folder. It
  never touches another context's tables.
- **Concurrent upgrades are serialised for the whole run.** `alembic upgrade heads` runs (web,
  worker, replicas) take a session-level advisory lock in `alembic/env.py`, polled
  (`pg_try_advisory_lock`) before the first migration transaction and held by the connection
  until the run ends (KAN-92, KAN-179, ADR-134); never wait for it in SQL — a waiting statement's
  snapshot deadlocks a `CREATE INDEX CONCURRENTLY`. An index on a large or hot table is built
  or dropped `CONCURRENTLY` inside `op.get_context().autocommit_block()`, in a migration of its
  own: what precedes the block has committed when it runs, so a failure leaves the schema
  part-way and an `INVALID` index behind — begin with `DROP INDEX CONCURRENTLY IF EXISTS` so a
  rerun repairs it, and declare the index on the model as usual. Everything else stays one
  transaction per upgrade (no `transaction_per_migration`).
  `tests/test_migration_lock_spans_autocommit_blocks.py` pins the lock across such a block.
- A stub context has no chain yet: its first migration needs a new labelled branch root and its
  folder added to `version_locations` in `alembic.ini` — part of the plan.
- **A migration is part of the plan** you show Anto before building (CLAUDE.md, "Plan before
  code").
- **Migrations already on `main` are immutable** — every environment that ran one would
  silently diverge. A hook blocks every way of changing one (editor, `sed`, `cp`, `mv`, `git mv`
  / `git rm`, redirects). Fix a bad migration with a new, corrective migration.
- **No cross-context foreign keys** (rule 2). A reference to another context is the
  three-column pattern (GUID + owner comment, display label, `labelRefreshedAt`) — never a
  `ForeignKey` into another context's table.
- **A new model module is imported in `app/model_registry.py`.** Autogenerate
  (`alembic/env.py`), the test schema and every script that imports it see only the tables it registers;
  `tests/architecture/test_model_registry_imports_every_model.py` fails on one it misses (KAN-187).
- **A newly required column or request field is a breaking API contract change**: it needs an
  explicit decision (the D-03/D-04 precedent: one migration, breaking, decided up front), never
  a side effect of a fix.
- **A data step in a migration is guarded for offline mode** (KAN-174). Under `alembic upgrade …
  --sql` every `execute` on `op.get_bind()` returns `None`, so a migration that reads rows
  (`conn.execute(…)` iterated, `.all()`, `.fetchall()`, `.scalar()`) crashes the render. A new
  migration checks `context.is_offline_mode()` first and, when it is set, skips the data step and
  emits a comment instead (`op.execute("-- <step> skipped: it reads rows and needs a live
  database")`); DDL renders as usual. The trunk's `b7c1e4a92f10` and the data migrations after it
  (every chain has at least one) predate this and are immutable, so an offline render crossing them
  fails — README's "Database migrations" names what still renders.
- **A migration's seed rows are literals in the migration** (KAN-517, ADR-133), never read from
  an application module that keeps moving: an edit there would give a fresh database other rows
  than a deployed one. Keep an equality test between the literal rows and the application's
  declaration (KAN-216's `994244560c8a` and `tests/test_migrated_notification_catalogue.py`).
  Four shipped platform migrations predate this and import their rows (holidays, standard texts,
  business lists, countries); being immutable, their sources are frozen by digest instead —
  `tests/architecture/test_migration_seed_sources_are_frozen.py`, which also fails on any other
  migration importing a bounded context. A change to a frozen source is a new data migration, and
  the new edition gets its own name; never update a digest.
- **Backfills run for real, on Postgres, and are verified by query**: count rows, count nulls,
  count values identical to the column they were derived from. An offline `--sql` render is not
  a run. A downgrade that is deliberately a no-op says so in a comment.
- **Data-migration scripts** (`scripts/migrate_*.py`, `scripts/repoint_*.py`,
  `scripts/backfill_*.py`) are dry-run by default (`--commit` writes), idempotent, and reuse the
  production computations instead of re-implementing them. A historical import **publishes no
  outbox events**: live consumers would act on old history as if it happened today (see the
  `migrate_transaction_rows.py` docstring). A backfill that repairs current state through the
  production write path publishes what that path publishes
  (`backfill_manual_configuration_reservations.py`, KAN-166: `inventory.stock_item.reserved` /
  `released`). The dry run reports what the real run would write, including rows it cannot
  migrate and why. It gets there by running the real path in one transaction it rolls back —
  flushing, so a later row sees an earlier one — and refuses any commit while it runs
  (`migrate_transaction_rows.py`, KAN-263); a dry run that skips the writes cannot see
  conflicts between rows of the same pass.
  That one transaction is the one place a script may write two contexts (Sales and Inventory)
  together, import Inventory's internals directly, and read the legacy `vehicle` row and the
  customer, dealership and `vehicle_mdm` rows it resolves against through their models — the four
  things ADR-091 (KAN-106) names, bounded to `migrate_transaction_rows.py` alone and pinned by the
  ADR-047 architecture test, which also names the three other scripts that reach two contexts'
  internals (no ruling yet); a new one fails by name.
- **Enum columns** are `StoredEnum` (`app/core/enum_type.py`), never a bare `sqlalchemy.Enum`
  (an architecture test enforces it), and hold the member's **`.value`** and nothing else
  (KAN-86, finished by KAN-175 on 2026-10-11: one `*_kan175_<context>_enum_values_only.py`
  per chain swept the last names). Raw SQL compares against the value (`status = 'pending'`).
- **Every enum column carries `ck_{table}_{column}_enum`** — `"{column}" IN ({values})` — which
  `StoredEnum` attaches to the model. Autogenerate renders it once in a new table's
  `create_table` (KAN-297), but it does not compare CHECK constraints: a migration that **adds an
  enum column to an existing table, or a member**, creates (or drops and recreates) that
  constraint by hand with `enum_check_name` / `enum_check_condition`'s exact name and wording.
  `tests/test_migrated_enum_checks.py` runs `alembic upgrade heads` on a throwaway database and
  fails on any `ck_*_enum` that differs from the models' (KAN-296).
- **The migrated schema is the models' schema, exactly** (KAN-170/KAN-251, no allowlist):
  `tests/test_migrated_schema_matches_models.py` runs `alembic upgrade heads` on a throwaway
  database and fails on anything `alembic check` would report — a migration-only index, a
  constraint the model names differently, a comment one side lacks — and, since KAN-414, on an
  index both sides have whose stored definition (`pg_get_indexdef`: a partial index's `WHERE`
  included) differs, which `alembic check` does not see, and since KAN-431 on an index only one
  side has by name on a table both have — a primary key or unique constraint named differently,
  which `compare_metadata` matches by columns. Declare every index and
  constraint on the model with the migration's exact name and shape; a column comment the
  model declares and the migration omitted is fixed by a comment-only migration in that
  context's chain (`*_column_comments_match_the_models_kan_170.py` is the pattern).
- The legacy `vehicle` table is write-frozen (ADR-021, `legacy_vehicle_write_frozen`); new code
  never writes it.
- `scripts/dev/check` mirrors both CI migration jobs whenever migrations, models or anything a
  migration run loads change (every such `app/` file, derived at run time — KAN-300): upgrade
  from empty → seed → downgrade to CI's target → upgrade → two concurrent upgrades (KAN-92), and
  upgrade from main's heads → seed → upgrade → verify the seeded rows survived. A `downgrade()`
  is exercised, so it must work.
