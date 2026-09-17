"""KAN-60: `2b7e1d4a9c30` (D-21, "customer.preferred_channel vocabulary")
renamed the vocabulary with a lowercase `WHERE preferred_channel = 'mail'`
clause, but `Customer.preferred_channel` is `SAEnum(PreferredChannel,
native_enum=False)` with no `values_callable` override, so SQLAlchemy
stores the enum MEMBER NAME (uppercase: EMAIL, PHONE, POST, ...), not
`.value`. D-21's rename never matched a single row, on any environment —
confirmed live in production by the traceback this ticket is named for:
`LookupError: 'MAIL' is not among the defined enum values.`

Two things are exercised here:
1. `a3f8d2c91b64`'s `_rename` actually converts the real (uppercase)
   legacy values, both directions — the migration KAN-60 shipped to do
   what D-21 was supposed to.
2. `list_customers` survives a row that still holds an undecodable value
   (whether from before this migration ran, or any future rename that
   makes the same mistake) — one corrupt customer must never take the
   whole list down for every other customer at the same dealer.
"""

import datetime as dt
import importlib.util
import pathlib
import uuid

from sqlalchemy import text

from app.core.pagination import SortPageParams
from app.core.sorting import SortField
from app.customer.models.customer import Customer, CustomerType, Language
from app.customer.services import customer as customer_service

_MIG_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "alembic/versions/customer/a3f8d2c91b64_fix_d21_case_mismatch.py"
)
_spec = importlib.util.spec_from_file_location("kan60_fix_migration", _MIG_PATH)
_mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mig)


def _insert_raw_customer(db_session, *, customer_number: str, preferred_channel: str) -> uuid.UUID:
    """Inserts a customer row via raw SQL with an arbitrary string in
    preferred_channel — bypassing the ORM's own Enum coercion entirely, the
    same way a genuinely legacy row (written before PreferredChannel had
    its current members) would look today. `Customer(...)` + `db.add(...)`
    cannot produce this state: SQLAlchemy's Enum type validates on write
    too, so only a raw INSERT can reproduce what a stale migration leaves
    behind.
    """

    customer_id = uuid.uuid4()
    now = dt.datetime.now(dt.UTC)
    db_session.execute(
        text(
            "INSERT INTO customer "
            "(id, group_id, customer_number, customer_type, language, lifecycle_status, "
            "marketing_consent, credit_block, gender, newsletter, vat_registered, version, "
            "created_at, updated_at, preferred_channel) "
            "VALUES "
            "(:id, :group_id, :customer_number, :customer_type, :language, :lifecycle_status, "
            ":marketing_consent, :credit_block, :gender, :newsletter, :vat_registered, :version, "
            ":created_at, :updated_at, :preferred_channel)"
        ),
        {
            "id": str(customer_id),
            "group_id": str(uuid.uuid4()),
            "customer_number": customer_number,
            "customer_type": "individual",
            "language": "de",
            "lifecycle_status": "prospect",
            "marketing_consent": False,
            "credit_block": False,
            "gender": "unspecified",
            "newsletter": False,
            "vat_registered": False,
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "preferred_channel": preferred_channel,
        },
    )
    db_session.commit()
    return customer_id


# --- the migration itself ----------------------------------------------------------


def test_upgrade_converts_the_real_uppercase_legacy_values(db_session):
    _insert_raw_customer(db_session, customer_number="K-MAIL", preferred_channel="MAIL")
    _insert_raw_customer(db_session, customer_number="K-CALL", preferred_channel="CALL")
    _insert_raw_customer(db_session, customer_number="K-LETTER", preferred_channel="LETTER")
    _insert_raw_customer(db_session, customer_number="K-MESSAGE", preferred_channel="MESSAGE")

    _mig._rename(db_session.connection(), _mig._RENAMES_UP)
    db_session.commit()

    rows = dict(
        db_session.execute(
            text("SELECT customer_number, preferred_channel FROM customer")
        ).all()
    )
    assert rows["K-MAIL"] == "EMAIL"
    assert rows["K-CALL"] == "PHONE"
    assert rows["K-LETTER"] == "POST"
    assert rows["K-MESSAGE"] == "MESSAGE"  # no clean target (KAN-54) — left untouched


def test_downgrade_reverses_it(db_session):
    _insert_raw_customer(db_session, customer_number="K-EMAIL", preferred_channel="EMAIL")

    _mig._rename(db_session.connection(), _mig._RENAMES_DOWN)
    db_session.commit()

    value = db_session.execute(
        text("SELECT preferred_channel FROM customer WHERE customer_number = 'K-EMAIL'")
    ).scalar_one()
    assert value == "MAIL"


def test_rename_is_idempotent_a_second_run_changes_nothing_further(db_session):
    _insert_raw_customer(db_session, customer_number="K-MAIL", preferred_channel="MAIL")

    _mig._rename(db_session.connection(), _mig._RENAMES_UP)
    db_session.commit()
    _mig._rename(db_session.connection(), _mig._RENAMES_UP)  # a re-run must not touch EMAIL rows
    db_session.commit()

    value = db_session.execute(
        text("SELECT preferred_channel FROM customer WHERE customer_number = 'K-MAIL'")
    ).scalar_one()
    assert value == "EMAIL"


# --- list_customers survives a row it can't decode ---------------------------------


def test_list_customers_omits_the_bad_row_but_returns_everyone_else_in_the_same_group(db_session):
    group_id = uuid.uuid4()
    good = Customer(
        group_id=group_id, customer_number="K-GOOD", customer_type=CustomerType.INDIVIDUAL,
        language=Language.DE, first_name="Ada", last_name="Lovelace",
    )
    db_session.add(good)
    db_session.commit()

    bad_id = uuid.uuid4()
    now = dt.datetime.now(dt.UTC)
    db_session.execute(
        text(
            "INSERT INTO customer "
            "(id, group_id, customer_number, customer_type, language, lifecycle_status, "
            "marketing_consent, credit_block, gender, newsletter, vat_registered, version, "
            "created_at, updated_at, preferred_channel) "
            "VALUES "
            "(:id, :group_id, :customer_number, :customer_type, :language, :lifecycle_status, "
            ":marketing_consent, :credit_block, :gender, :newsletter, :vat_registered, :version, "
            ":created_at, :updated_at, :preferred_channel)"
        ),
        {
            "id": str(bad_id), "group_id": str(group_id), "customer_number": "K-BAD",
            "customer_type": "individual", "language": "de", "lifecycle_status": "prospect",
            "marketing_consent": False, "credit_block": False, "gender": "unspecified",
            "newsletter": False, "vat_registered": False, "version": 1,
            "created_at": now, "updated_at": now, "preferred_channel": "MAIL",
        },
    )
    db_session.commit()

    params = SortPageParams(
        limit=50, cursor=None,
        sort_fields=[SortField(api_name="customerNumber", column=Customer.customer_number, direction="asc", nullable=False)],
    )
    items, _next_cursor, _total, _total_is_estimate = customer_service.list_customers(
        db_session, group_id=group_id, q=None, lifecycle_status=None, customer_type=None, language=None,
        canton=None, updated_since=None, params=params,
    )

    assert [c.customer_number for c in items] == ["K-GOOD"]
