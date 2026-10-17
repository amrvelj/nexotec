"""KAN-197: the tax_id -> uid conversion inside the platform migration
4f8a2c7e1d93, proven on its pure helpers with a real Fernet round trip —
one valid encrypted tax_id, one that fails the check digit, one that does
not decrypt, one duplicate, one NULL. The CI job "alembic upgrade from
previously-deployed revision" runs the same conversion against a seeded
Postgres (scripts/seed_migration_smoke_test.py seeds a valid and an invalid
encrypted tax_id; scripts/verify_migration_smoke_test_seed.py checks both).
"""

import importlib.util
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.core.validators import validate_swiss_uid

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "platform"
    / "4f8a2c7e1d93_kan197_dealership_column_corrections.py"
)


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location("kan197_migration", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("plaintext", "expected"),
    [
        ("CHE-987.654.326", "CHE-987.654.326"),
        (" che-123.456.788 ", "CHE-123.456.788"),
        ("CHE-000.000.000", "CHE-000.000.000"),
    ],
)
def test_frozen_uid_rule_accepts_what_the_live_validator_accepts(migration, plaintext, expected):
    assert migration.uid_from_tax_id(plaintext) == (expected, None)
    assert validate_swiss_uid(plaintext) == expected


@pytest.mark.parametrize(
    ("plaintext", "reason"),
    [
        ("CHE-999.999.999", "fails the UID check digit"),
        ("CHE-123.456.789", "fails the UID check digit"),
        ("123.456.788", "is not in the format CHE-123.456.789"),
        ("", "is not in the format CHE-123.456.789"),
        ("CHE-\uff11\uff12\uff13.456.788", "is not in the format CHE-123.456.789"),
    ],
)
def test_frozen_uid_rule_rejects_what_the_live_validator_rejects(migration, plaintext, reason):
    assert migration.uid_from_tax_id(plaintext) == (None, reason)
    with pytest.raises(ValueError):
        validate_swiss_uid(plaintext)


def test_conversion_writes_valid_first_seen_uids_and_reports_the_rest(migration):
    fernet = Fernet(Fernet.generate_key())
    other = Fernet(Fernet.generate_key())

    def enc(value: str, key: Fernet = fernet) -> str:
        return key.encrypt(value.encode("utf-8")).decode("ascii")

    rows = [
        ("id-valid", "Garage Valid AG", enc("CHE-987.654.326")),
        ("id-bad-check", "Garage Check GmbH", enc("CHE-999.999.999")),
        ("id-garbage", "Garage Garbage SA", enc("not a uid")),
        ("id-other-key", "Garage Other Key AG", enc("CHE-123.456.788", other)),
        ("id-duplicate", "Garage Dup AG", enc("che-987.654.326")),
        ("id-null", "Garage Null AG", None),
        ("id-second-valid", "Garage Two AG", enc("CHE-123.456.788")),
    ]

    updates, report = migration.convert_rows(
        rows, lambda ciphertext: fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    )

    assert updates == [("id-valid", "CHE-987.654.326"), ("id-second-valid", "CHE-123.456.788")]
    assert len(report) == 5
    by_id = {line.split()[3]: line for line in report}
    assert "fails the UID check digit" in by_id["id-bad-check"]
    assert "is not in the format" in by_id["id-garbage"]
    assert "does not decrypt" in by_id["id-other-key"]
    assert "repeats the UID of dealership id-valid" in by_id["id-duplicate"]
    assert "tax_id is NULL" in by_id["id-null"]
    assert all(line.startswith("KAN-197 REPORT: dealership ") and line.endswith("uid left NULL") for line in report)
    # The UID is public (D-A-22): the report names the value so the operator can act.
    assert "'CHE-999.999.999'" in by_id["id-bad-check"]


def test_conversion_aborts_when_nothing_decrypts_because_the_key_is_wrong(migration):
    right = Fernet(Fernet.generate_key())
    wrong = Fernet(Fernet.generate_key())
    rows = [
        ("id-1", "Garage Eins AG", right.encrypt(b"CHE-987.654.326").decode("ascii")),
        ("id-2", "Garage Zwei AG", right.encrypt(b"CHE-123.456.788").decode("ascii")),
        ("id-null", "Garage Null AG", None),
    ]
    with pytest.raises(RuntimeError, match="none of the 2 encrypted tax_id value"):
        migration.convert_rows(rows, lambda c: wrong.decrypt(c.encode("ascii")).decode("utf-8"))


def test_conversion_does_not_abort_when_only_some_rows_fail_to_decrypt(migration):
    right = Fernet(Fernet.generate_key())
    wrong = Fernet(Fernet.generate_key())
    rows = [
        ("id-1", "Garage Eins AG", right.encrypt(b"CHE-987.654.326").decode("ascii")),
        ("id-2", "Garage Zwei AG", wrong.encrypt(b"CHE-123.456.788").decode("ascii")),
    ]
    updates, report = migration.convert_rows(rows, lambda c: right.decrypt(c.encode("ascii")).decode("utf-8"))
    assert updates == [("id-1", "CHE-987.654.326")]
    assert len(report) == 1 and "does not decrypt" in report[0]
