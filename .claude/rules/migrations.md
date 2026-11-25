---
paths:
  - "alembic/**"
  - "alembic.ini"
  - "app/**/models/**"
  - "app/core/*_model.py"
  - "app/model_registry.py"
  - "app/core/enum_type.py"
  - "app/core/text_collation.py"
  - "app/db.py"
  - "tests/**/test_migrated_*.py"
  - "tests/test_migration_*.py"
  - "tests/architecture/test_migration_seed_sources_are_frozen.py"
  - "tests/architecture/test_model_registry_imports_every_model.py"
  - "tests/architecture/test_enum_columns_use_stored_enum.py"
  - "scripts/check_concurrent_alembic_upgrade.py"
---
<!-- Maintainer note. ADR-015 (PR-3 chains), ADR-011, ADR-021, ADR-088, ADR-133, ADR-134, rule 2. KAN-615
(2026-11-13) cut it to the invariants; data-migration scripts are migrations-scripts.md. Last
verified against main@32ad509 (2026-11-25). -->

# Schema and migrations

- **`alembic upgrade heads` — plural.** One chain per context under `alembic/versions/<context>/`,
  each rooted in a labelled branch root off the frozen shared trunk (`b36486886126`; the trunk's
  revisions never move) (ADR-015). Tables of `app/core/*_model.py` go in the `core` chain. A new
  migration descends from its own context's branch — `alembic revision -m "…" --head
  customer@head` (`--autogenerate` when models changed) — and never touches another context's
  tables. A stub context's first migration needs a new labelled branch root and its folder in
  `alembic.ini`'s `version_locations`.
- **A migration is part of the plan** shown before building. **Migrations already on `main` are
  immutable** (a hook blocks every way of changing one): fix a bad one with a corrective migration.
- **Concurrent upgrades are serialised for the whole run** (ADR-134): `alembic/env.py` takes a
  session-level advisory lock, polled with `pg_try_advisory_lock`, held until the run ends — never
  wait for it in SQL (a waiting statement's snapshot deadlocks a `CREATE INDEX CONCURRENTLY`). An
  index on a large or hot table is built or dropped `CONCURRENTLY` inside
  `op.get_context().autocommit_block()`, in a migration of its own: what precedes the block has
  committed, so a failure leaves an `INVALID` index — begin with `DROP INDEX CONCURRENTLY IF
  EXISTS` so a rerun repairs it, and declare the index on the model. Everything else is one
  transaction per upgrade (`tests/test_migration_lock_spans_autocommit_blocks.py`).
- **No cross-context foreign keys** (rule 2): another context's id is the three-column pattern
  (GUID + owner comment, label, `labelRefreshedAt`), never a `ForeignKey`.
- **A new model module is imported in `app/model_registry.py`** — autogenerate, the test schema and
  every script see only what it registers (`test_model_registry_imports_every_model.py`).
- **A newly required column or request field is a breaking contract change**: an explicit decision
  up front (the D-03/D-04 precedent), never a side effect of a fix.
- **A data step is guarded for offline mode:** under `--sql` every `execute` on `op.get_bind()`
  returns `None`, so a migration reading rows checks `context.is_offline_mode()` first and emits a
  comment instead (`op.execute("-- <step> skipped: it reads rows and needs a live database")`);
  DDL renders as usual. Older data migrations break an offline render across them (README,
  "Database migrations").
- **A migration's seed rows are literals in the migration** (ADR-133), never read from an
  application module that keeps moving; keep an equality test between them and the application's
  declaration (`tests/platform/test_migrated_notification_catalogue.py` is the pattern). Four shipped platform
  migrations import their rows; their sources are frozen by digest
  (`tests/architecture/test_migration_seed_sources_are_frozen.py`, which also refuses a migration
  importing a bounded context). A change to a frozen source is a new data migration with its own
  name; never update a digest.
- **Enum columns are `StoredEnum`** (`app/core/enum_type.py`; an architecture test), holding the
  member's **`.value`** only; raw SQL compares the value (`status = 'pending'`). Each carries
  `ck_{table}_{column}_enum`. Autogenerate renders it in a new table but does not compare CHECKs:
  a migration adding an enum column to an existing table, or a member, writes the constraint by
  hand with `enum_check_name`/`enum_check_condition`'s exact name and wording
  (`tests/test_migrated_enum_checks.py`).
- **The migrated schema is the models' schema, exactly** (no allowlist):
  `tests/test_migrated_schema_matches_models.py` fails on anything `alembic check` reports, on an
  index whose stored definition differs (a partial index's `WHERE` included), and on an index or
  constraint only one side has by name. Declare every index and constraint on the model with the
  migration's exact name and shape; a missing column comment is fixed by a comment-only migration
  in that context's chain.
- **An index behind a word sort** is over `text_sort_index_key` (`app/core/text_collation.py`, KAN-740,
  ADR-190): `alembic/env.py`'s `include_object` keeps it out of autogenerate and `alembic check`, so
  its migration is hand-written and it is compared by `pg_get_indexdef`
  (`test_text_sort_indexes_use_the_collation.py`).
- `Settings.legacy_vehicle_write_frozen` is read by nothing since the legacy `vehicle` table went
  (ADR-021 amended); its removal waits on a preserved-file edit (`config.py`).
- `scripts/dev/check` mirrors both CI migration jobs when migrations, models or what a migration
  run loads change (empty → seed → downgrade to CI's target → upgrade → two concurrent upgrades;
  main's heads → seed → upgrade → verify): a `downgrade()` is exercised, so it must work.
