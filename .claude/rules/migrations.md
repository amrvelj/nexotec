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
---
<!-- Maintainer note (stripped before Claude sees it). Summarises ADR-015 (PR-3 chains),
ADR-011, ADR-021, rule 2 and the verification lessons from the WP audits. Verified against
main@568f416 on 2026-09-28 (every present-tense claim checked against the code). Fix this file
in the same PR as any change to what it states. -->

# Schema, migrations and data migrations

- **`alembic upgrade heads` — plural.** There is one chain per context under
  `alembic/versions/<context>/`, each rooted in a branch-root revision labelled with the context
  (ADR-015, PR-3); the tables in `app/core/*_model.py` live in the `core` chain. A new migration
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
  run reports exactly what the real run would write, including rows it cannot migrate and why.
- **Enum columns:** SQLAlchemy persists enum member **names** by default, not values. A
  migration that rewrites enum data must write what the ORM can decode.
- The legacy `vehicle` table is write-frozen (ADR-021, `legacy_vehicle_write_frozen`); new code
  never writes it.
- `scripts/dev/check` mirrors both CI migration jobs whenever migrations, models or their
  imports change: upgrade from empty → seed → downgrade to CI's target → upgrade → two
  concurrent upgrades (KAN-92), and upgrade from main's heads → seed → upgrade → verify the
  seeded rows survived. A `downgrade()` is exercised, so it must work.
