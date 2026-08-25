"""Architecture test (WP-4, closes Gap Analysis G-09): "no password hash
remains anywhere in the database," verified with a query against the live
mapped schema — not by inspecting that app/platform/models/credential.py
was deleted, which proves the file is gone but not that nothing replaced
it. Same idiom as tests/test_acceptance_shell.py's own
test_ac6_schema_has_no_payment_card_ssn_or_drivers_license_columns:
introspect SQLAlchemy metadata directly, across every mapped table, not
just the ones this change touched.

Two checks, deliberately broader than the literal ask: the credential
table itself is gone, AND no column anywhere matches a password-shaped
name pattern — a standing regression guard against a future PR
reintroducing one elsewhere, not just a check that this one table stays
dropped.
"""

import re

import app.model_registry
from app.db import Base

_PASSWORD_LIKE_COLUMN_PATTERN = re.compile(r"password|pwd|bcrypt", re.IGNORECASE)


def test_credential_table_does_not_exist():
    assert app.model_registry  # ensures all model modules are imported, metadata populated
    assert "credential" not in Base.metadata.tables


def test_no_password_hash_shaped_column_exists_anywhere():
    assert app.model_registry
    violations = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if _PASSWORD_LIKE_COLUMN_PATTERN.search(column.name)
    ]
    assert violations == []
