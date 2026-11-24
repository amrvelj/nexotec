"""KAN-46 backfill: the data-repair migration
alembic/versions/customer/a1c46e02b7f9_backfill_contact_channel_primary.py
finds contact type-groups left with zero or several USABLE primaries and
repairs them by the same rule the write path now applies
(_fixup_single_primary). Exercised here against seeded broken state, since
production data on this branch has none.
"""

import datetime as dt
import importlib.util
import pathlib
import uuid

from app.contact.models.contact import (
    Contact,
    ContactPhone,
    ContactType,
    Language,
    PhoneType,
)
from tests.postgres_only import POSTGRES_SQL
from tests.pre_rename_names import pre_rename_names

_MIG_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic/versions/customer/a1c46e02b7f9_backfill_contact_channel_primary.py"
)
_spec = importlib.util.spec_from_file_location("kan46_backfill_migration", _MIG_PATH)
_mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mig)


def _contact(db_session) -> Contact:
    c = Contact(
        group_id=uuid.uuid4(),
        contact_number=f"K-{uuid.uuid4().hex[:6]}",
        contact_type=ContactType.INDIVIDUAL,
        language=Language.DE,
        first_name="Ada",
        last_name="Lovelace",
    )
    db_session.add(c)
    db_session.flush()
    return c


def _phone(db_session, contact, e164, *, is_primary, days, valid_to=None, do_not_use=False) -> ContactPhone:
    base = dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
    p = ContactPhone(
        group_id=contact.group_id,
        contact_id=contact.id,
        phone_type=PhoneType.MOBILE,
        phone_e164=e164,
        phone_normalised=e164.lstrip("+"),
        is_primary=is_primary,
        valid_to=valid_to,
        do_not_use=do_not_use,
        created_at=base + dt.timedelta(days=days),
        updated_at=base + dt.timedelta(days=days),
        created_by=uuid.uuid4(),
        updated_by=uuid.uuid4(),
    )
    db_session.add(p)
    db_session.flush()
    return p


def _run(db_session) -> dict[str, int]:
    # The migration names the tables as they were before KAN-464 PR-3.
    with pre_rename_names(db_session.connection()):
        return _mig._repair_table(db_session.connection(), "customer_phone", "phone_type")


def _reload(db_session, *phones) -> list[bool]:
    for p in phones:
        db_session.refresh(p)
    return [p.is_primary for p in phones]


@POSTGRES_SQL
def test_zero_usable_primary_group_gets_the_oldest_usable_promoted(db_session):
    c = _contact(db_session)
    older = _phone(db_session, c, "+41791111111", is_primary=False, days=0)
    newer = _phone(db_session, c, "+41792222222", is_primary=False, days=1)

    counts = _run(db_session)

    assert counts["zero_usable_primary"] == 1
    assert _reload(db_session, older, newer) == [True, False]


@POSTGRES_SQL
def test_dead_primary_with_a_usable_survivor_hands_the_flag_over(db_session):
    c = _contact(db_session)
    dead = _phone(
        db_session, c, "+41791111111", is_primary=True, days=0,
        valid_to=dt.datetime(2023, 1, 1, tzinfo=dt.UTC),
    )
    alive = _phone(db_session, c, "+41792222222", is_primary=False, days=1)

    counts = _run(db_session)

    assert counts["demoted_dead_primary"] == 1
    assert counts["zero_usable_primary"] == 1
    assert _reload(db_session, dead, alive) == [False, True]


@POSTGRES_SQL
def test_multiple_usable_primaries_collapse_to_the_oldest(db_session):
    c = _contact(db_session)
    a = _phone(db_session, c, "+41791111111", is_primary=True, days=0)
    b = _phone(db_session, c, "+41792222222", is_primary=True, days=1)

    counts = _run(db_session)

    assert counts["multiple_usable_primary"] == 1
    assert _reload(db_session, a, b) == [True, False]


@POSTGRES_SQL
def test_group_with_no_usable_row_elects_nothing(db_session):
    c = _contact(db_session)
    dead = _phone(
        db_session, c, "+41791111111", is_primary=True, days=0, do_not_use=True,
    )

    counts = _run(db_session)

    assert counts["demoted_dead_primary"] == 1
    assert counts["zero_usable_primary"] == 0
    assert _reload(db_session, dead) == [False]


@POSTGRES_SQL
def test_healthy_group_is_left_untouched(db_session):
    c = _contact(db_session)
    primary = _phone(db_session, c, "+41791111111", is_primary=True, days=0)
    other = _phone(db_session, c, "+41792222222", is_primary=False, days=1)

    counts = _run(db_session)

    assert counts == {"demoted_dead_primary": 0, "multiple_usable_primary": 0, "zero_usable_primary": 0}
    assert _reload(db_session, primary, other) == [True, False]
