"""Architecture test (WP-4, inverted by D-A-07 / KAN-196): password-shaped
columns exist in `credential` and NOWHERE else, and no table other than
`credential` (and, later, the terminal realm's `device_technician_pin` —
ADR-028) holds a credential.

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

from sqlalchemy import UniqueConstraint

import app.model_registry
from app.db import Base

# A technician's terminal PIN (ADR-028) is a credential too: `pin` as a
# whole word of a column name (`pin_hash`, `pin_code`), never a substring
# (`shipping`, `mapping`).
_PASSWORD_LIKE_COLUMN_PATTERN = re.compile(r"password|pwd|bcrypt|argon|(^|_)pin(_|$)", re.IGNORECASE)
# The tables allowed to hold a credential: the staff realm's today; the
# terminal realm's PIN table when ADR-028 ships (KAN-508, Anto 2026-10-31:
# `WORKSHOP_TERMINAL_AUTH_DESIGN.md` § Data model names it
# `device_technician_pin`; the device table, `device_terminal`, holds only a
# secrets-manager reference). A third name here is a ruling.
_CREDENTIAL_TABLES = {"credential", "device_technician_pin"}


def _password_shaped_columns_outside_the_credential_tables(tables) -> list[str]:
    return [
        f"{table.name}.{column.name}"
        for table in tables
        for column in table.columns
        if _PASSWORD_LIKE_COLUMN_PATTERN.search(column.name) and table.name not in _CREDENTIAL_TABLES
    ]


def test_the_credential_table_exists_with_its_password_column():
    assert app.model_registry
    table = Base.metadata.tables["credential"]
    assert "password_hash" in table.columns
    # One credential set per user: a unique constraint on exactly user_id. Since
    # KAN-431 the model names it (`uq_credential_user_id`, the migration's
    # name) in __table_args__ instead of `unique=True` on the column, so the
    # ruling is read from the table's constraints — the same ruling.
    unique_column_sets = [
        {column.name for column in constraint.columns}
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert {"user_id"} in unique_column_sets or table.columns["user_id"].unique, "one credential set per user"


def test_password_shaped_columns_live_in_credential_and_nowhere_else():
    assert app.model_registry
    assert _password_shaped_columns_outside_the_credential_tables(Base.metadata.tables.values()) == []


def test_the_terminal_pin_is_allowed_only_in_the_table_the_design_names():
    """KAN-508: the terminal realm's PIN hash lives in `device_technician_pin`
    (the design's data model), so that table is the allowed one, and a PIN
    column anywhere else — the device table included — is refused like a
    password column. Shown on a throwaway schema: neither table exists yet."""

    from sqlalchemy import Column, MetaData, String, Table

    sketch = MetaData()
    pin_table = Table("device_technician_pin", sketch, Column("pin_hash", String))
    device_table = Table("device_terminal", sketch, Column("pin_hash", String), Column("secret_ref", String))
    unrelated = Table("stock_item_sketch", sketch, Column("shipping_note", String), Column("mapping_gap", String))

    assert _password_shaped_columns_outside_the_credential_tables([pin_table, unrelated]) == []
    assert _password_shaped_columns_outside_the_credential_tables([device_table]) == ["device_terminal.pin_hash"]


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
