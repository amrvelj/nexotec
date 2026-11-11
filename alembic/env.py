import time
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from sqlalchemy import text
from sqlalchemy.engine import Connection

from alembic import context

import app.model_registry  # noqa: F401  registers every model on Base.metadata
from app.core.config import get_settings
from app.core.enum_type import render_autogenerate_item
from app.db import Base, with_psycopg_driver

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Postgres advisory-lock key that serialises concurrent `alembic upgrade heads`
# runs against one database (KAN-92). Any fixed signed 64-bit value works; this
# one is the first 8 bytes of sha256(b"nexotec:alembic-upgrade"), big-endian,
# signed. Advisory locks are per database; nothing else may take this key.
MIGRATION_ADVISORY_LOCK_KEY = -7906448801533702249
# How often a waiting upgrader asks for the lock again (KAN-179).
MIGRATION_LOCK_POLL_INTERVAL_S = 0.25

# Real connection string comes from app settings (DMS_DATABASE_URL env var),
# not the placeholder in alembic.ini — keeps one source of truth. Normalized
# through the same with_psycopg_driver() as app/db.py's engine: managed
# Postgres providers (Render's connectionString included) hand out a bare
# postgres://ish URL that SQLAlchemy would otherwise default to psycopg2,
# which isn't installed here (see app/db.py for the full rationale).
config.set_main_option("sqlalchemy.url", with_psycopg_driver(get_settings().database_url))

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


# StoredEnum columns render as plain VARCHAR, their CHECKs as named
# sa.CheckConstraint — see the function's docstring (KAN-86, KAN-417).
render_item = render_autogenerate_item


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_item=render_item,
    )

    with context.begin_transaction():
        context.run_migrations()


def _take_migration_lock(connection: Connection) -> None:
    """Polls for the migration lock with no transaction open between tries
    (see run_migrations_online for why it is never waited for in SQL)."""

    while True:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": MIGRATION_ADVISORY_LOCK_KEY}
        ).scalar_one()
        connection.commit()
        if acquired:
            return
        time.sleep(MIGRATION_LOCK_POLL_INTERVAL_S)


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Every service that starts from this codebase runs `alembic upgrade
        # heads` first: the web service in render.yaml, both `app` and
        # `worker` in docker-compose.yml, the Dockerfile's default CMD and
        # every web replica (the worker is declared in render.yaml too, but
        # commented out: not provisioned on staging, KAN-234). Without this
        # lock two overlapping runs both start the same migration and the
        # second exits 1 (KAN-92).
        #
        # The lock is the connection's (session-level `pg_advisory_lock`),
        # held from before the first migration transaction until the run
        # ends (KAN-179, ADR-134). A transaction-scoped lock was released by
        # the first commit, so a migration that commits mid-run - an
        # `op.get_context().autocommit_block()`, which `CREATE INDEX
        # CONCURRENTLY` needs - let a second run into the same migration;
        # held by the connection it spans every commit. The waiting run takes
        # it before its migration transaction begins, so it reads
        # alembic_version after the winner committed, whatever the isolation
        # level. It is polled (`pg_try_advisory_lock`), never waited for in
        # a statement: a waiting statement holds a snapshot, and `CREATE
        # INDEX CONCURRENTLY` in the winner waits for every older snapshot to
        # end - a deadlock Postgres resolves by failing the waiter (measured).
        # Between tries no transaction is open. Each try opens SQLAlchemy's
        # autobegin transaction, which is committed at once: left open,
        # Alembic would treat it as external and never commit the migrations
        # (measured under KAN-92: exit 0, nothing migrated). The connection
        # is closed at the end (NullPool), which releases the lock even when
        # a migration fails; the unlock says so explicitly on success.
        # tests/test_migration_lock_spans_autocommit_blocks.py and
        # scripts/check_concurrent_alembic_upgrade.py are the proof.
        locked = connection.dialect.name == "postgresql"
        if locked:
            _take_migration_lock(connection)

        context.configure(
            connection=connection, target_metadata=target_metadata, render_item=render_item
        )

        with context.begin_transaction():
            context.run_migrations()

        if locked:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_ADVISORY_LOCK_KEY})
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
