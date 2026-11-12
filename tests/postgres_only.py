"""KAN-651: the accepted Postgres-only skips (`.claude/rules/tests.md`), each
with the reason SQLite cannot run the test faithfully. They skip only on the
SQLite fast lane (`DMS_TEST_DATABASE_URL` unset); the lane of record, Postgres,
runs every one of them (ADR-011).

Never apply one to make a failure go away whose cause is not its reason.
"""

import os

import pytest

_ON_SQLITE = not os.environ.get("DMS_TEST_DATABASE_URL")

# The fast lane runs every session on ONE in-memory DBAPI connection
# (tests/conftest.py, StaticPool): a second connection would be a different,
# empty database, and a file database allows one writer at a time. Code that
# commits a session of its own while the caller's transaction is open — the
# provider gateway's call log (KAN-90), committed while the caller holds a
# savepoint (ADR-123's per-detail savepoint, `_load_one`) — therefore commits
# the caller's transaction too, and the caller's RELEASE SAVEPOINT fails («no
# such savepoint»). On Postgres each session has its own connection.
NEEDS_ITS_OWN_CONNECTION = pytest.mark.skipif(
    _ON_SQLITE,
    reason="commits or rolls back a session of its own while another is open: the SQLite lane shares one "
    "in-memory connection between sessions (KAN-651)",
)

# Postgres SQL with no SQLite equivalent in the test or the migration it runs
# (pg_constraint, ::regnamespace, now()).
POSTGRES_SQL = pytest.mark.skipif(_ON_SQLITE, reason="runs Postgres-only SQL (KAN-651)")

# `timestamptz`: Postgres compares instants across time zones; SQLite stores
# a datetime as text and compares the strings, so a Zurich midnight and the
# same instant in UTC are not equal there.
TIMESTAMPTZ = pytest.mark.skipif(
    _ON_SQLITE, reason="compares time-zone-aware timestamps: SQLite stores them as text (KAN-651)"
)

# Row locks held across concurrent sessions (FOR UPDATE, SKIP LOCKED):
# SQLite has none, and its one shared connection has no second session.
ROW_LOCKS = pytest.mark.skipif(
    _ON_SQLITE, reason="row locks across concurrent sessions are Postgres-only (KAN-651)"
)

# pysqlite runs its own transaction handling: it issues no BEGIN before a
# SAVEPOINT, so releasing the outermost savepoint commits, and the rollback
# that follows undoes nothing (a dry run, a read-only preview that seeds a row
# under a savepoint). SQLAlchemy's documented remedy (the driver's isolation
# level off, BEGIN on begin) breaks the shared connection above: two sessions
# would both BEGIN on it.
SAVEPOINT_ROLLBACK = pytest.mark.skipif(
    _ON_SQLITE, reason="rolls back work done under a savepoint: pysqlite commits on its release (KAN-651)"
)

# Case-insensitive matching beyond ASCII (`ILIKE` on «Bären» for «BÄREN»):
# Postgres folds case by the database's ctype; SQLite's LIKE folds ASCII
# letters only (KAN-652).
NON_ASCII_CASE_FOLDING = pytest.mark.skipif(
    _ON_SQLITE, reason="folds case beyond ASCII: SQLite's LIKE folds ASCII letters only (KAN-652)"
)
