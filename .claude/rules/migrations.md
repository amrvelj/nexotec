---
paths:
  - "alembic/**"
  - "alembic.ini"
  - "app/**/models/**"
  - "app/core/*_model.py"
  - "app/model_registry.py"
  - "scripts/migrate_*.py"
  - "scripts/repoint_*.py"
  - "scripts/*migration_smoke_test*.py"
  - "app/db.py"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises ADR-015 (PR-3 chains),
ADR-011, ADR-021, rule 2 and the verification lessons from the WP audits. Verified against
main@7805816 on 2026-10-10 (every present-tense claim checked against the code). Fix this file
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
- **One upgrade is one transaction.** Concurrent `alembic upgrade heads` runs (web, worker,
  replicas) are serialised by a transaction-scoped advisory lock in `alembic/env.py` (KAN-92).
  A migration that commits mid-run — `op.get_context().autocommit_block()`, e.g. `CREATE INDEX
  CONCURRENTLY` — or turning on `transaction_per_migration` releases that lock early: part of
  the plan, with the lock reworked in the same PR.
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
- **A newly required column or request field is a breaking API contract change**: it needs an
  explicit decision (the D-03/D-04 precedent: one migration, breaking, decided up front), never
  a side effect of a fix.
- **Backfills run for real, on Postgres, and are verified by query**: count rows, count nulls,
  count values identical to the column they were derived from. An offline `--sql` render is not
  a run. A downgrade that is deliberately a no-op says so in a comment.
- **Data-migration scripts** (`scripts/migrate_*.py`) are dry-run by default (`--commit`
  writes), idempotent, and reuse the production computations instead of re-implementing them —
  but a historical backfill **publishes no outbox events**: live consumers would act on old
  history as if it happened today (see the `migrate_transaction_rows.py` docstring). The dry
  run reports what the real run would write, including rows it cannot migrate and why — but a
  dry run that does not flush cannot see conflicts between rows of the same pass
  (`migrate_transaction_rows.py`'s cannot; the `--commit` report is authoritative there).
- **Enum columns** are `StoredEnum` (`app/core/enum_type.py`), never a bare `sqlalchemy.Enum`
  (an architecture test enforces it), and hold the member's **`.value`** and nothing else
  (KAN-86, finished by KAN-175 on 2026-10-11: one `*_kan175_<context>_enum_values_only.py`
  per chain swept the last names). Raw SQL compares against the value (`status = 'pending'`).
- **Every enum column carries `ck_{table}_{column}_enum`** — `"{column}" IN ({values})` — which
  `StoredEnum` attaches to the model. Autogenerate renders it once in a new table's
  `create_table` (KAN-297), but it does not compare CHECK constraints: a migration that **adds an
  enum column to an existing table, or a member**, creates (or drops and recreates) that
  constraint by hand with `enum_check_name` / `enum_check_condition`'s exact name and wording. `tests/test_migrated_enum_checks.py` runs `alembic upgrade heads` on a
  throwaway database and fails on any `ck_*_enum` that differs from the models' (KAN-296).
- The legacy `vehicle` table is write-frozen (ADR-021, `legacy_vehicle_write_frozen`); new code
  never writes it.
- `scripts/dev/check` mirrors both CI migration jobs whenever migrations, models or their
  imports change: upgrade from empty → seed → downgrade to CI's target → upgrade → two
  concurrent upgrades (KAN-92), and upgrade from main's heads → seed → upgrade → verify the
  seeded rows survived. A `downgrade()` is exercised, so it must work.
