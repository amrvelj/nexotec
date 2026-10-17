"""Architecture test (WP-4, inverted by D-A-07 / KAN-196): password-shaped
columns exist in `credential` and NOWHERE else, and no table other than
`credential` (and, later, `terminal_device` — ADR-028) holds a credential.

WP-4 wrote the first version of this guard the other way round — "no
password hash remains anywhere in the database" — when Zitadel owned the
front door. Nexotec owns authentication again (D-A-07), so the same guard
now points the other way: the one credential table must exist, its
password column must exist, and nothing else may grow one. A customer
credential (D-A-13, reserved) can only ever be a second table named here,
never new columns on `credential`. Verified with a query against the live
mapped schema, across every mapped table.
"""

import re

import app.model_registry
from app.db import Base

_PASSWORD_LIKE_COLUMN_PATTERN = re.compile(r"password|pwd|bcrypt|argon", re.IGNORECASE)
# The tables allowed to hold a credential: the staff realm's today; the
# terminal realm's when ADR-028 ships. A third name here is a ruling.
_CREDENTIAL_TABLES = {"credential", "terminal_device"}


def test_the_credential_table_exists_with_its_password_column():
    assert app.model_registry
    table = Base.metadata.tables["credential"]
    assert "password_hash" in table.columns
    assert table.columns["user_id"].unique, "one credential set per user"


def test_password_shaped_columns_live_in_credential_and_nowhere_else():
    assert app.model_registry
    elsewhere = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if _PASSWORD_LIKE_COLUMN_PATTERN.search(column.name) and table.name not in _CREDENTIAL_TABLES
    ]
    assert elsewhere == []


def test_no_second_credential_table_appears_unannounced():
    """A table named like a credential store that is not in the allowlist
    is a new realm — an ADR, not a migration."""

    assert app.model_registry
    suspicious = [
        name
        for name in Base.metadata.tables
        if re.search(r"credential|password", name, re.IGNORECASE) and name not in _CREDENTIAL_TABLES
    ]
    assert suspicious == []
