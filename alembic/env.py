from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from sqlalchemy import text

from alembic import context

import app.model_registry  # noqa: F401  registers every model on Base.metadata
from app.core.config import get_settings
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
    )

    with context.begin_transaction():
        context.run_migrations()


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
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            # The web service, the outbox worker and every web replica run
            # `alembic upgrade heads` on start (render.yaml, docker-compose.yml,
            # Dockerfile CMD). Without this lock two overlapping runs both start
            # the same migration and the second exits 1 (KAN-92). It is taken
            # here, as the first statement inside Alembic's own transaction, and
            # released when that transaction commits: the second run waits, then
            # finds the schema current. Keep it here. Executed on the connection
            # before context.configure(), SQLAlchemy's autobegin opens a
            # transaction Alembic treats as external and never commits (measured:
            # exit 0, nothing migrated); a session-level pg_advisory_lock would
            # outlive the transaction it protects.
            # Two assumptions hold the guarantee: the whole upgrade is ONE
            # transaction (no transaction_per_migration, no autocommit_block()
            # such as CREATE INDEX CONCURRENTLY - either commits mid-run and
            # releases the lock), and READ COMMITTED isolation (under REPEATABLE
            # READ the waiting run's snapshot predates the winner's commit, so it
            # would read a stale alembic_version).
            if connection.dialect.name == "postgresql":
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": MIGRATION_ADVISORY_LOCK_KEY},
                )
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
