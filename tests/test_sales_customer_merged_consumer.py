"""KAN-185 (ADR-047; Anto, 2026-10-31, option A): a customer merge writes
nothing of Sales'. It publishes `customer.merged` in its own transaction;
Sales' consumer `sales.customer_merged` moves the duplicate's legacy
transactions, offers, contracts and deals to the survivor in its own
transaction, idempotent by eventId.

Before KAN-185 the legacy rows moved inside the merge's transaction (the
one tracked ADR-047 debt) and offers/contracts/deals in an own-commit call
after it.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.consumer import consume_once
from app.core.outbox_model import OutboxMessage
from app.core.processed_event_model import ProcessedEvent
from app.customer.schemas.customer import CustomerCreate, CustomerEmailCreate
from app.customer.services import customer as customer_service
from app.sales.consumers import handle_customer_merged_message
from app.sales.models.contract import SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.offer import SalesOffer
from app.sales.models.transaction import Transaction, TransactionStatus, TransactionType
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import create_contract
from app.sales.services.offer import create_offer, update_offer
from app.worker import register_handlers
from tests.test_consumer_redelivery_idempotency import _RecordingTransport

CONSUMER = "sales.customer_merged"


def _customer(db: Session, group_id: uuid.UUID, tenant_id: uuid.UUID, name: str):
    return customer_service.create_customer(
        db,
        group_id=group_id,
        data=CustomerCreate(
            customer_type="individual", language="de", first_name=name, last_name="Keller",
            emails=[
                CustomerEmailCreate(
                    email_type="personal", email_address=f"{name.lower()}-{uuid.uuid4().hex[:6]}@example.ch",
                    is_primary=True,
                )
            ],
        ),
        actor_id=uuid.uuid4(),
        dealership_id=tenant_id,
    )


def _offer(db: Session, tenant_id: uuid.UUID, group_id: uuid.UUID, customer_id: uuid.UUID) -> SalesOffer:
    offer = create_offer(db, tenant_id=tenant_id, actor_id=uuid.uuid4())
    return update_offer(db, offer=offer, group_id=group_id, data=OfferUpdate(customer_id=customer_id), actor_id=uuid.uuid4())


def _legacy_transaction(db: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> Transaction:
    # ADR-050: the table is retired for business writes; seeded through the ORM.
    txn = Transaction(
        tenant_id=tenant_id, transaction_type=TransactionType.SALE, status=TransactionStatus.DRAFT,
        customer_id=customer_id, vehicle_id=uuid.uuid4(), primary_user_id=uuid.uuid4(),
    )
    db.add(txn)
    db.commit()
    return txn


def _merge(db: Session, *, duplicate_id: uuid.UUID, survivor_id: uuid.UUID, group_id: uuid.UUID) -> OutboxMessage:
    loaded = customer_service.get_customer_or_404(db, group_id, duplicate_id)
    customer_service.merge_customer(db, customer=loaded, duplicate_of_customer_id=survivor_id, actor_id=uuid.uuid4())
    return db.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "customer.merged", OutboxMessage.aggregate_id == duplicate_id
        )
    ).one()


def _deliver(db: Session, message: OutboxMessage) -> bool:
    return consume_once(db, message=message, consumer_name=CONSUMER, handler=handle_customer_merged_message)


def _customer_ids(db: Session, *rows) -> list[uuid.UUID]:
    db.expire_all()
    return [db.get(type(row), row.id).customer_id for row in rows]


def test_the_consumer_is_registered_for_customer_merged():
    transport = _RecordingTransport()
    register_handlers(transport)

    assert transport.registrations[CONSUMER][0] == "customer.merged"


def test_the_merge_writes_nothing_of_sales_and_the_consumer_moves_it_all_once(db_session):
    tenant_id, group_id = uuid.uuid4(), uuid.uuid4()
    survivor = _customer(db_session, group_id, tenant_id, "Survivor")
    duplicate = _customer(db_session, group_id, tenant_id, "Duplicate")
    txn = _legacy_transaction(db_session, tenant_id, duplicate.id)
    offer = _offer(db_session, tenant_id, group_id, duplicate.id)
    contracted_offer = _offer(db_session, tenant_id, group_id, duplicate.id)
    contract = create_contract(db_session, tenant_id=tenant_id, offer=contracted_offer, actor_id=uuid.uuid4())
    deals = list(db_session.scalars(select(SalesDeal).where(SalesDeal.customer_id == duplicate.id)).all())
    assert len(deals) == 2  # the open offer's row and the contract's

    message = _merge(db_session, duplicate_id=duplicate.id, survivor_id=survivor.id, group_id=group_id)

    # The merge committed its own work only: every Sales row still names the duplicate.
    assert _customer_ids(db_session, txn, offer, contracted_offer, contract, *deals) == [duplicate.id] * 6
    assert message.tenant_id == group_id

    assert _deliver(db_session, message) is True
    assert _customer_ids(db_session, txn, offer, contracted_offer, contract, *deals) == [survivor.id] * 6

    # Redelivered (at-least-once): a no-op, one processed_event row.
    assert _deliver(db_session, message) is False
    assert (
        len(
            db_session.scalars(
                select(ProcessedEvent).where(
                    ProcessedEvent.event_id == message.id, ProcessedEvent.consumer_name == CONSUMER
                )
            ).all()
        )
        == 1
    )


def test_an_earlier_merge_delivered_late_lands_on_the_live_survivor(db_session):
    """X→D, then D→T; the worker delivers D→T first. X→D's payload names D,
    merged by then: X's offer follows D's merge chain to T, never a merged
    customer."""

    tenant_id, group_id = uuid.uuid4(), uuid.uuid4()
    t = _customer(db_session, group_id, tenant_id, "Target")
    d = _customer(db_session, group_id, tenant_id, "Dora")
    x = _customer(db_session, group_id, tenant_id, "Xaver")
    x_offer = _offer(db_session, tenant_id, group_id, x.id)
    d_offer = _offer(db_session, tenant_id, group_id, d.id)

    x_into_d = _merge(db_session, duplicate_id=x.id, survivor_id=d.id, group_id=group_id)
    d_into_t = _merge(db_session, duplicate_id=d.id, survivor_id=t.id, group_id=group_id)

    assert _deliver(db_session, d_into_t) is True
    assert _deliver(db_session, x_into_d) is True

    assert _customer_ids(db_session, x_offer, d_offer) == [t.id, t.id]
    assert db_session.scalars(select(SalesOffer).where(SalesOffer.customer_id.in_([x.id, d.id]))).all() == []
    assert db_session.scalars(select(SalesContract).where(SalesContract.customer_id.in_([x.id, d.id]))).all() == []


def test_the_moved_rows_take_the_survivors_label_and_language(db_session):
    """KAN-580: the consumer moved `customer_id` and left `customer_label` —
    the Sales list and the detail screens kept naming the duplicate, a
    customer that no longer exists as a record (rule 2: a label follows its
    owner). The survivor is read from `customer.public`, never from the
    payload; its correspondence language moves with the label (they share
    one refresh stamp, as `apply_customer_change` writes them)."""

    tenant_id, group_id = uuid.uuid4(), uuid.uuid4()
    survivor = _customer(db_session, group_id, tenant_id, "Survivor")
    survivor.language = type(survivor.language)("fr")
    db_session.commit()
    duplicate = _customer(db_session, group_id, tenant_id, "Duplicate")
    offer = _offer(db_session, tenant_id, group_id, duplicate.id)
    contracted_offer = _offer(db_session, tenant_id, group_id, duplicate.id)
    contract = create_contract(db_session, tenant_id=tenant_id, offer=contracted_offer, actor_id=uuid.uuid4())
    deals = list(db_session.scalars(select(SalesDeal).where(SalesDeal.customer_id == duplicate.id)).all())
    assert {row.customer_label for row in (offer, contracted_offer, contract, *deals)} == {"Duplicate Keller"}

    message = _merge(db_session, duplicate_id=duplicate.id, survivor_id=survivor.id, group_id=group_id)
    assert _deliver(db_session, message) is True

    db_session.expire_all()
    moved = [db_session.get(type(row), row.id) for row in (offer, contracted_offer, contract, *deals)]
    assert [row.customer_label for row in moved] == ["Survivor Keller"] * 5
    assert all(row.customer_denorm_refreshed_at is not None for row in moved)
    assert [row.customer_language for row in moved[:3]] == ["fr"] * 3


def test_a_survivor_renamed_after_the_merge_event_is_read_as_it_stands_now(db_session):
    """KAN-580: never the payload — the label is the survivor's as the
    consumer finds it (delivery is unordered and may be late)."""

    tenant_id, group_id = uuid.uuid4(), uuid.uuid4()
    survivor = _customer(db_session, group_id, tenant_id, "Survivor")
    duplicate = _customer(db_session, group_id, tenant_id, "Duplicate")
    offer = _offer(db_session, tenant_id, group_id, duplicate.id)
    message = _merge(db_session, duplicate_id=duplicate.id, survivor_id=survivor.id, group_id=group_id)
    survivor = customer_service.get_customer_or_404(db_session, group_id, survivor.id)
    survivor.first_name = "Renamed"
    db_session.commit()

    assert _deliver(db_session, message) is True

    db_session.expire_all()
    assert db_session.get(SalesOffer, offer.id).customer_label == "Renamed Keller"
