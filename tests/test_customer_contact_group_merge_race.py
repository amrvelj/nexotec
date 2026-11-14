"""KAN-466: a contact-group edit on a customer while another customer is merged
into it — the contact-group counterpart of KAN-390's tag race
(tests/test_customer_tag_merge_race.py).

`update_customer` inserts the new `customer_contact_group` rows; a merge into
the same customer moves the duplicate's groups onto it
(`contact_groups.repoint_contact_groups`). When both name the same group, the
PATCH's insert hits `uq_customer_contact_group_customer_id_value_code` inside
the flush guard and is refused 409 `customer_contact_group_exists`, never a
500 and never the generic conflict.
"""

import threading
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.errors import ConflictError
from app.customer.models.customer import Customer, CustomerContactGroup, CustomerType, Language
from app.customer.schemas.customer import CustomerUpdate
from app.customer.services import contact_groups as contact_groups_service
from app.customer.services import customer as customer_service
from app.customer.services import customer_common as customer_common_service
from app.customer.services import customer_merge as customer_merge_service
from app.platform.public import ContactGroupCodes
from tests.test_customer_vehicle_party_close_rule import _postgres_only, _run, _wait_until_a_backend_waits_on_a_lock

_CODES = frozenset({"fleet", "leasing"})


@pytest.fixture(autouse=True)
def _acting_betrieb_has_both_groups_on(monkeypatch):
    """The race is about the constraint, not the switch: the acting Betrieb
    may set both groups (the switch itself is tests/test_contact_groups.py's)."""

    monkeypatch.setattr(
        contact_groups_service, "contact_group_codes", lambda db, dealership_id: ContactGroupCodes(_CODES, _CODES)
    )


def _customer(db_session, group_id: uuid.UUID, first_name: str, groups: tuple[str, ...] = ()) -> Customer:
    customer = Customer(
        group_id=group_id, customer_number=f"K-{uuid.uuid4().hex[:6]}", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name=first_name, last_name="Muster",
    )
    db_session.add(customer)
    db_session.flush()
    for code in groups:
        db_session.add(CustomerContactGroup(group_id=group_id, customer_id=customer.id, value_code=code))
    db_session.flush()
    return customer


def _merge(db, duplicate_id: uuid.UUID, survivor_id: uuid.UUID, group_id: uuid.UUID) -> None:
    loaded = customer_common_service.get_customer_or_404(db, group_id, duplicate_id)
    customer_merge_service.merge_customer(db, customer=loaded, duplicate_of_customer_id=survivor_id, actor_id=uuid.uuid4())


def _set_groups(db, customer_id: uuid.UUID, group_id: uuid.UUID, groups: list[str]) -> None:
    loaded = customer_common_service.get_customer_or_404(db, group_id, customer_id)
    customer_service.update_customer(
        db, customer=loaded, data=CustomerUpdate(contact_groups=groups), actor_id=uuid.uuid4(), dealership_id=uuid.uuid4()
    )


def _groups(db_session, customer_id: uuid.UUID) -> list[str]:
    db_session.expire_all()
    return sorted(
        db_session.scalars(
            select(CustomerContactGroup.value_code).where(CustomerContactGroup.customer_id == customer_id)
        ).all()
    )


def _pause_merge(monkeypatch, *, after_the_groups: bool):
    """Pauses the merge thread after it has locked the survivor: after its
    group rows reached the database (its audit row's flush), or just before
    it moves them."""

    reached, release = threading.Event(), threading.Event()

    def _pause():
        if threading.current_thread().name == "merge-x-into-t":
            reached.set()
            release.wait(timeout=10)

    if after_the_groups:
        real_record = customer_merge_service.record_audit_event

        def _record_then_pause(db, **kwargs):
            result = real_record(db, **kwargs)
            if kwargs["action"] == "merge":
                _pause()
            return result

        monkeypatch.setattr(customer_merge_service, "record_audit_event", _record_then_pause)
    else:
        real_repoint = contact_groups_service.repoint_contact_groups

        def _pause_then_repoint(db, **kwargs):
            _pause()
            return real_repoint(db, **kwargs)

        monkeypatch.setattr(contact_groups_service, "repoint_contact_groups", _pause_then_repoint)
    return reached, release


@pytest.mark.parametrize("after_the_groups", [True, False], ids=["groups-moved", "groups-not-yet-moved"])
def test_a_contact_group_patch_racing_a_merge_that_brings_the_same_group_is_a_409_never_a_500(
    engine, db_session, monkeypatch, after_the_groups
):
    """X (in «leasing») is merged into T and paused before committing, holding
    T's row FOR SHARE (KAN-372). A PATCH adding «leasing» to T waits for it;
    once the merge commits, the PATCH's insert collides with the moved row.
    The PATCH is refused 409 `customer_contact_group_exists`; the merge
    stands, and T keeps the one «leasing» row the merge gave it."""

    _postgres_only(engine)
    group_id = uuid.uuid4()
    x = _customer(db_session, group_id, "Xaver", groups=("leasing",))
    t = _customer(db_session, group_id, "Tina", groups=("fleet",))
    db_session.commit()
    x_id, t_id = x.id, t.id
    reached, release = _pause_merge(monkeypatch, after_the_groups=after_the_groups)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []
    refusals: list[ConflictError] = []

    def _merge_x_into_t():
        with factory() as session:
            _merge(session, x_id, t_id, group_id)

    def _patch_t():
        with factory() as session:
            try:
                _set_groups(session, t_id, group_id, ["fleet", "leasing"])
            except ConflictError as exc:
                refusals.append(exc)

    merge = _run("merge-x-into-t", errors, _merge_x_into_t)
    try:
        assert reached.wait(timeout=10), "the merge never reached its pause"
        patch = _run("patch-t", errors, _patch_t)
        _wait_until_a_backend_waits_on_a_lock(engine)  # the PATCH waits on the merge
        release.set()
        merge.join(timeout=10)
        patch.join(timeout=10)
    finally:
        release.set()

    assert not merge.is_alive() and not patch.is_alive()
    assert errors == []
    assert [r.details["reason"] for r in refusals] == ["customer_contact_group_exists"]
    assert _groups(db_session, t_id) == ["fleet", "leasing"]
    assert db_session.get(Customer, x_id).duplicate_of_customer_id == t_id


def test_an_integrity_failure_that_is_not_the_group_constraint_is_not_called_a_group_collision(engine, db_session):
    """The guard names `customer_contact_group_exists` only for
    uq_customer_contact_group_customer_id_value_code; any other constraint the
    flush trips (a NOT NULL column the PATCH clears, built with
    model_construct to get past the schema) keeps the generic 409."""

    _postgres_only(engine)
    group_id = uuid.uuid4()
    t = _customer(db_session, group_id, "Tina")
    db_session.commit()

    with pytest.raises(ConflictError) as refused:
        loaded = customer_common_service.get_customer_or_404(db_session, group_id, t.id)
        customer_service.update_customer(
            db_session, customer=loaded, data=CustomerUpdate.model_construct(newsletter=None), actor_id=uuid.uuid4(),
            dealership_id=uuid.uuid4(),
        )

    assert (refused.value.details or {}).get("reason") != "customer_contact_group_exists"
