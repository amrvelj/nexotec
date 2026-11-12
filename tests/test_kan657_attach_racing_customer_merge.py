"""KAN-657: a buyer attach racing a customer merge never leaves the deal on the
duplicate, and the merge's Sales consumer never overwrites a buyer an attach
changed meanwhile.

Before, `apply_customer_change` read the customer without any lock and the
`sales.customer_merged` consumer selected the duplicate's rows without one: an
attach could read the duplicate as active, the merge commit, the consumer run
while the attach was still uncommitted and miss the deal, and the attach then
commit on the merged record. Now both take Sales' own advisory lock on the
customer (`services/customer_attach_lock.py`) — the attach before its row
lock, the consumer before it reads — and the consumer reads the rows FOR
UPDATE. Two sessions, Postgres only (advisory and row locks).
"""

import threading

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.customer.models.customer import Customer
from app.sales import consumers as sales_consumers
from app.sales.consumers import handle_customer_merged_message
from app.sales.models.offer import SalesOffer
from app.sales.services import offer as offer_service
from app.sales.services.customer_attach_lock import lock_customer_for_attach
from app.sales.services.offer import apply_customer_change, create_offer, get_offer_or_404
from tests.test_customer_vehicle_party_close_rule import (
    _customer,
    _merge,
    _run,
    _wait_until_a_backend_waits_on_a_lock,
)
from tests.test_sales_lifecycle_reservation import _dealership


@pytest.fixture
def _postgres(engine):
    if engine.dialect.name != "postgresql":
        pytest.skip("advisory and row locks are Postgres-only; the SQLite lane serialises writers itself")


def _setup(db_session, *, offer_on_duplicate: bool = False):
    dealership = _dealership(db_session)
    group_id = dealership.dealer_group_id
    duplicate = _customer(db_session, group_id, "Dora")
    survivor = _customer(db_session, group_id, "Sven")
    other = _customer(db_session, group_id, "Olga")
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=None)
    if offer_on_duplicate:
        apply_customer_change(db_session, deal=offer, group_id=group_id, customer_id=duplicate.id, host="an offer")
    db_session.commit()
    return dealership.id, group_id, duplicate.id, survivor.id, other.id, offer.id


def _attach(session, tenant_id, group_id, offer_id, customer_id) -> None:
    """What the PATCH route does: the attach lock, then the offer's row lock."""

    lock_customer_for_attach(session, customer_id)
    offer = get_offer_or_404(session, tenant_id, offer_id, for_update=True)
    apply_customer_change(session, deal=offer, group_id=group_id, customer_id=customer_id, host="an offer")
    session.commit()


def _consume_merge(session, duplicate_id) -> None:
    message = session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "customer.merged", OutboxMessage.aggregate_id == duplicate_id
        )
    ).one()
    handle_customer_merged_message(session, message)
    session.commit()


def _pause_attach_after_its_customer_read(monkeypatch):
    reached, release = threading.Event(), threading.Event()
    real_refuse = offer_service.refuse_unattachable_customer

    def _pause(db, **kwargs):
        real_refuse(db, **kwargs)
        if threading.current_thread().name == "attach" and not reached.is_set():
            reached.set()
            release.wait(timeout=10)

    monkeypatch.setattr(offer_service, "refuse_unattachable_customer", _pause)
    return reached, release


def test_an_attach_in_flight_during_the_merge_is_moved_to_the_survivor(_postgres, engine, db_session, monkeypatch):
    """The attach read the duplicate as active and is paused before its
    commit; the merge commits meanwhile. The consumer waits for the attach,
    then finds the deal on the duplicate and moves it."""

    tenant_id, group_id, duplicate_id, survivor_id, _other, offer_id = _setup(db_session)
    reached, release = _pause_attach_after_its_customer_read(monkeypatch)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []

    def _attaching():
        with factory() as session:
            _attach(session, tenant_id, group_id, offer_id, duplicate_id)

    def _consuming():
        with factory() as session:
            _consume_merge(session, duplicate_id)

    attach = _run("attach", errors, _attaching)
    try:
        assert reached.wait(timeout=10), "the attach never read its customer"
        with factory() as merging:
            _merge(merging, duplicate_id, survivor_id, group_id)  # commits; takes no Sales lock
        consumer = _run("consumer", errors, _consuming)
        _wait_until_a_backend_waits_on_a_lock(engine)  # the consumer waits on the attach's lock
        release.set()
        attach.join(timeout=10)
        consumer.join(timeout=10)
    finally:
        release.set()

    assert not attach.is_alive() and not consumer.is_alive()
    assert errors == []
    db_session.expire_all()
    assert db_session.get(Customer, duplicate_id).duplicate_of_customer_id == survivor_id
    assert db_session.get(SalesOffer, offer_id).customer_id == survivor_id


def test_an_attach_while_the_consumer_moves_the_duplicates_deals_waits_and_is_refused(
    _postgres, engine, db_session, monkeypatch
):
    tenant_id, group_id, duplicate_id, survivor_id, _other, offer_id = _setup(db_session)
    with factory_session(engine) as merging:
        _merge(merging, duplicate_id, survivor_id, group_id)
    reached, release = threading.Event(), threading.Event()
    real_repoint = sales_consumers.repoint_merged_customer_records

    def _pause_in_the_consumer(db, **kwargs):
        if threading.current_thread().name == "consumer" and not reached.is_set():
            reached.set()
            release.wait(timeout=10)
        return real_repoint(db, **kwargs)

    monkeypatch.setattr(sales_consumers, "repoint_merged_customer_records", _pause_in_the_consumer)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []
    refusals: list[ConflictError] = []

    def _attaching():
        with factory() as session:
            try:
                _attach(session, tenant_id, group_id, offer_id, duplicate_id)
            except ConflictError as exc:
                refusals.append(exc)

    def _consuming():
        with factory() as session:
            _consume_merge(session, duplicate_id)

    consumer = _run("consumer", errors, _consuming)
    try:
        assert reached.wait(timeout=10), "the consumer never reached its re-point"
        attach = _run("attach", errors, _attaching)
        _wait_until_a_backend_waits_on_a_lock(engine)  # the attach waits on the consumer's lock
        release.set()
        consumer.join(timeout=10)
        attach.join(timeout=10)
    finally:
        release.set()

    assert not attach.is_alive() and not consumer.is_alive()
    assert errors == []
    assert [r.details["reason"] for r in refusals] == ["customer_merged"]
    db_session.expire_all()
    assert db_session.get(SalesOffer, offer_id).customer_id is None


def test_the_consumer_never_overwrites_a_buyer_changed_while_it_waited(_postgres, engine, db_session, monkeypatch):
    """The offer names the duplicate; an attach of another customer holds the
    offer's row while the merge's consumer runs. The consumer waits for the
    row and then leaves it alone — it no longer names the duplicate. Before,
    it updated the row by id and replaced the new buyer with the survivor."""

    tenant_id, group_id, duplicate_id, survivor_id, other_id, offer_id = _setup(db_session, offer_on_duplicate=True)
    reached, release = _pause_attach_after_its_customer_read(monkeypatch)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []

    def _attaching():
        with factory() as session:
            _attach(session, tenant_id, group_id, offer_id, other_id)

    def _consuming():
        with factory() as session:
            _consume_merge(session, duplicate_id)

    attach = _run("attach", errors, _attaching)
    try:
        assert reached.wait(timeout=10), "the attach never read its customer"
        with factory() as merging:
            _merge(merging, duplicate_id, survivor_id, group_id)
        consumer = _run("consumer", errors, _consuming)
        _wait_until_a_backend_waits_on_a_lock(engine)  # the consumer waits on the offer's row
        release.set()
        attach.join(timeout=10)
        consumer.join(timeout=10)
    finally:
        release.set()

    assert not attach.is_alive() and not consumer.is_alive()
    assert errors == []
    db_session.expire_all()
    assert db_session.get(SalesOffer, offer_id).customer_id == other_id


def factory_session(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def test_an_offer_copy_in_flight_during_the_merge_is_moved_to_the_survivor(_postgres, engine, db_session, monkeypatch):
    """«Offerte kopieren» names the source's customer. A copy that read the
    duplicate and is paused before its commit makes the consumer wait, and
    the consumer then moves the copy as well as the source."""

    tenant_id, group_id, duplicate_id, survivor_id, _other, source_id = _setup(db_session, offer_on_duplicate=True)
    reached, release = threading.Event(), threading.Event()
    real_copy_configuration = offer_service._copy_configuration

    def _pause_after_the_source_read(db, **kwargs):
        if threading.current_thread().name == "copy" and not reached.is_set():
            reached.set()
            release.wait(timeout=10)
        return real_copy_configuration(db, **kwargs)

    monkeypatch.setattr(offer_service, "_copy_configuration", _pause_after_the_source_read)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []
    copies: list = []

    def _copying():
        with factory() as session:
            source = get_offer_or_404(session, tenant_id, source_id)
            copies.append(offer_service.copy_offer(session, source=source, actor_id=None).id)

    def _consuming():
        with factory() as session:
            _consume_merge(session, duplicate_id)

    copy = _run("copy", errors, _copying)
    try:
        assert reached.wait(timeout=10), "the copy never read its source"
        with factory() as merging:
            _merge(merging, duplicate_id, survivor_id, group_id)
        consumer = _run("consumer", errors, _consuming)
        _wait_until_a_backend_waits_on_a_lock(engine)  # the consumer waits on the copy's lock
        release.set()
        copy.join(timeout=10)
        consumer.join(timeout=10)
    finally:
        release.set()

    assert not copy.is_alive() and not consumer.is_alive()
    assert errors == []
    db_session.expire_all()
    assert db_session.get(SalesOffer, source_id).customer_id == survivor_id
    assert db_session.get(SalesOffer, copies[0]).customer_id == survivor_id
