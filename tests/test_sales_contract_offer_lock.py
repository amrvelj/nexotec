"""KAN-147: one live contract per offer, held by the offer's row lock in
create_contract — and that lock taken in the same order (offer, then deal)
as every other Sales writer, the customer merge's repointing included.

Row-lock interleaving needs Postgres; SQLite has no row locks.
"""

import os
import threading
import time
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

import app.sales.services.contract as contract_module
from app.core.errors import ConflictError
from app.customer.schemas.customer import CustomerCreate, CustomerEmailCreate
from app.customer.services.customer import create_customer
from app.sales.models.contract import SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.offer import SalesOffer
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import create_contract
from app.sales.services.customer_merge import repoint_customer_sales_records
from app.sales.services.offer import create_offer, update_offer

pytestmark = pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"), reason="row-lock interleaving needs Postgres"
)


def _offer_for_a_customer(db_session):
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    customer = create_customer(
        db_session, group_id=group_id,
        data=CustomerCreate(
            customer_type="individual", language="de", first_name="Lea", last_name="Keller",
            emails=[CustomerEmailCreate(email_type="personal", email_address="lea@example.ch", is_primary=True)],
        ),
        actor_id=uuid.uuid4(), dealership_id=tenant_id,
    )
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=group_id, data=OfferUpdate(customer_id=customer.id), actor_id=uuid.uuid4()
    )
    return tenant_id, customer.id, offer.id


def _wait_for_a_blocked_lock(engine, timeout: float = 10.0) -> bool:
    """True once some backend of this database waits on a lock — the
    other thread has reached the offer this creation holds."""

    deadline = time.monotonic() + timeout
    with engine.connect() as conn:
        while time.monotonic() < deadline:
            waiting = conn.execute(
                text("SELECT count(*) FROM pg_stat_activity "
                     "WHERE datname = current_database() AND wait_event_type = 'Lock'")
            ).scalar_one()
            if waiting:
                return True
            conn.rollback()
            time.sleep(0.05)
    return False


def _hold_the_offer_lock_while(other: threading.Thread, engine, blocked: list[bool]):
    """Patches allocate_contract_number — which create_contract calls right
    after locking the offer — to start `other` and go on only once Postgres
    shows it waiting on a lock (or after 10 s, recorded in `blocked`)."""

    real_allocate = contract_module.allocate_contract_number

    def allocate_after_letting_the_other_run(*args, **kwargs):
        if other.ident is None:
            other.start()
            blocked.append(_wait_for_a_blocked_lock(engine))
        return real_allocate(*args, **kwargs)

    return patch.object(contract_module, "allocate_contract_number", side_effect=allocate_after_letting_the_other_run)


def _join(other: threading.Thread) -> None:
    if other.ident is not None:
        other.join(timeout=10.0)


def test_two_concurrent_creations_from_one_offer_make_one_contract(db_session, engine):
    tenant_id, _customer_id, offer_id = _offer_for_a_customer(db_session)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    outcomes: list[object] = []

    def second_creation():
        session = factory()
        try:
            offer = session.get(SalesOffer, offer_id)
            outcomes.append(create_contract(session, tenant_id=tenant_id, offer=offer, actor_id=uuid.uuid4()).contract_number)
        except ConflictError as exc:
            outcomes.append(exc.details)
        except BaseException as exc:  # noqa: BLE001 — asserted below, never swallowed
            outcomes.append(exc)
        finally:
            session.close()

    other = threading.Thread(target=second_creation)
    blocked: list[bool] = []
    session = factory()
    try:
        with _hold_the_offer_lock_while(other, engine, blocked):
            first = create_contract(session, tenant_id=tenant_id, offer=session.get(SalesOffer, offer_id), actor_id=uuid.uuid4())
    finally:
        session.close()
        _join(other)

    assert blocked == [True]  # the other thread really waited on the offer's lock
    assert not other.is_alive()
    assert outcomes == [{"reason": "offer_already_contracted", "contractNumber": first.contract_number}]
    assert len(db_session.scalars(select(SalesContract).where(SalesContract.offer_id == offer_id)).all()) == 1


def test_a_customer_merge_during_a_contract_creation_does_not_deadlock(db_session, engine):
    """The merge used to write sales_deal before sales_offer in one flush;
    with create_contract holding the offer and about to write the deal, the
    two waited on each other until Postgres aborted one (KAN-147 review)."""

    tenant_id, customer_id, offer_id = _offer_for_a_customer(db_session)
    target_id = uuid.uuid4()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    errors: list[BaseException] = []

    def merge():
        session = factory()
        try:
            repoint_customer_sales_records(session, duplicate_id=customer_id, target_id=target_id)
        except BaseException as exc:  # noqa: BLE001 — asserted below, never swallowed
            errors.append(exc)
        finally:
            session.close()

    other = threading.Thread(target=merge)
    blocked: list[bool] = []
    session = factory()
    try:
        with _hold_the_offer_lock_while(other, engine, blocked):
            contract = create_contract(
                session, tenant_id=tenant_id, offer=session.get(SalesOffer, offer_id), actor_id=uuid.uuid4()
            )
    finally:
        session.close()
        _join(other)

    assert blocked == [True]  # the other thread really waited on the offer's lock
    assert not other.is_alive()
    assert errors == []
    db_session.expire_all()
    assert db_session.get(SalesOffer, offer_id).customer_id == target_id
    assert db_session.get(SalesContract, contract.id).customer_id == target_id
    assert {d.customer_id for d in db_session.scalars(select(SalesDeal).where(SalesDeal.offer_id == offer_id))} == {target_id}


def test_a_contract_copies_the_offer_as_committed_not_as_its_caller_loaded_it(db_session, engine):
    """KAN-147 review: create_contract re-reads the offer under its lock, so
    a customer merge committed after the caller loaded the offer is what the
    contract (and its deal) carry — not the duplicate customer."""

    tenant_id, customer_id, offer_id = _offer_for_a_customer(db_session)
    target_id = uuid.uuid4()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = factory()
    merge_session = factory()
    try:
        offer = session.get(SalesOffer, offer_id)
        assert offer.customer_id == customer_id
        session.commit()  # the caller's read is over; its object is now stale
        repoint_customer_sales_records(merge_session, duplicate_id=customer_id, target_id=target_id)

        contract = create_contract(session, tenant_id=tenant_id, offer=offer, actor_id=uuid.uuid4())
    finally:
        merge_session.close()
        session.close()

    assert contract.customer_id == target_id
    db_session.expire_all()
    assert {d.customer_id for d in db_session.scalars(select(SalesDeal).where(SalesDeal.offer_id == offer_id))} == {target_id}
