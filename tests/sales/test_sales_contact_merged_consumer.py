"""KAN-185 (ADR-047; Anto, 2026-10-31, option A): a contact merge writes
nothing of Sales'. It publishes `contact.merged` in its own transaction;
Sales' consumer `sales.contact_merged` moves the duplicate's offers,
contracts and deals to the survivor in its own transaction, idempotent by
eventId.

Before KAN-185 the legacy rows moved inside the merge's transaction (the
one tracked ADR-047 debt) and offers/contracts/deals in an own-commit call
after it. The legacy `transaction` table went with KAN-555.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contact.schemas.contact import ContactCreate, ContactEmailCreate
from app.contact.services import contact as contact_service
from app.contact.services import contact_common as contact_common_service
from app.contact.services import contact_merge as contact_merge_service
from app.core.consumer import consume_once
from app.core.outbox_model import OutboxMessage
from app.core.processed_event_model import ProcessedEvent
from app.sales.consumers import handle_contact_merged_message
from app.sales.models.contract import SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.offer import SalesOffer
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import create_contract
from app.sales.services.offer import create_offer, update_offer
from app.worker import register_handlers
from tests.cross_context.test_consumer_redelivery_idempotency import _RecordingTransport

CONSUMER = "sales.contact_merged"


def _contact(db: Session, group_id: uuid.UUID, tenant_id: uuid.UUID, name: str):
    return contact_service.create_contact(
        db,
        group_id=group_id,
        data=ContactCreate(
            contact_type="individual", language="de", first_name=name, last_name="Keller",
            emails=[
                ContactEmailCreate(
                    email_type="personal", email_address=f"{name.lower()}-{uuid.uuid4().hex[:6]}@example.ch",
                    is_primary=True,
                )
            ],
        ),
        actor_id=uuid.uuid4(), acting_dealership_id=None,
        dealership_id=tenant_id,
    )


def _offer(db: Session, tenant_id: uuid.UUID, group_id: uuid.UUID, contact_id: uuid.UUID) -> SalesOffer:
    offer = create_offer(db, tenant_id=tenant_id, actor_id=uuid.uuid4())
    return update_offer(db, offer=offer, group_id=group_id, data=OfferUpdate(contact_id=contact_id), actor_id=uuid.uuid4())


def _merge(db: Session, *, duplicate_id: uuid.UUID, survivor_id: uuid.UUID, group_id: uuid.UUID) -> OutboxMessage:
    loaded = contact_common_service.get_contact_or_404(db, group_id, duplicate_id)
    contact_merge_service.merge_contact(db, contact=loaded, duplicate_of_contact_id=survivor_id, actor_id=uuid.uuid4(), acting_dealership_id=None)
    return db.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "contact.merged", OutboxMessage.aggregate_id == duplicate_id
        )
    ).one()


def _deliver(db: Session, message: OutboxMessage) -> bool:
    return consume_once(db, message=message, consumer_name=CONSUMER, handler=handle_contact_merged_message)


def _contact_ids(db: Session, *rows) -> list[uuid.UUID]:
    db.expire_all()
    return [db.get(type(row), row.id).contact_id for row in rows]


def test_the_consumer_is_registered_for_contact_merged():
    transport = _RecordingTransport()
    register_handlers(transport)

    assert transport.registrations[CONSUMER][0] == "contact.merged"


def test_the_name_before_the_rename_is_no_longer_subscribed():
    """KAN-464 PR-5 (ADR-210): `customer.merged` and its consumer
    `sales.customer_merged` are retired; the merge reaches Sales as
    `contact.merged` only."""

    transport = _RecordingTransport()
    register_handlers(transport)

    assert "sales.customer_merged" not in transport.registrations
    assert all(event_type != "customer.merged" for event_type, _ in transport.registrations.values())


def test_the_merge_writes_nothing_of_sales_and_the_consumer_moves_it_all_once(db_session):
    tenant_id, group_id = uuid.uuid4(), uuid.uuid4()
    survivor = _contact(db_session, group_id, tenant_id, "Survivor")
    duplicate = _contact(db_session, group_id, tenant_id, "Duplicate")
    offer = _offer(db_session, tenant_id, group_id, duplicate.id)
    contracted_offer = _offer(db_session, tenant_id, group_id, duplicate.id)
    contract = create_contract(db_session, tenant_id=tenant_id, offer=contracted_offer, actor_id=uuid.uuid4())
    deals = list(db_session.scalars(select(SalesDeal).where(SalesDeal.contact_id == duplicate.id)).all())
    assert len(deals) == 2  # the open offer's row and the contract's

    message = _merge(db_session, duplicate_id=duplicate.id, survivor_id=survivor.id, group_id=group_id)

    # The merge committed its own work only: every Sales row still names the duplicate.
    assert _contact_ids(db_session, offer, contracted_offer, contract, *deals) == [duplicate.id] * 5
    assert message.tenant_id == group_id

    assert _deliver(db_session, message) is True
    assert _contact_ids(db_session, offer, contracted_offer, contract, *deals) == [survivor.id] * 5

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
    contact."""

    tenant_id, group_id = uuid.uuid4(), uuid.uuid4()
    t = _contact(db_session, group_id, tenant_id, "Target")
    d = _contact(db_session, group_id, tenant_id, "Dora")
    x = _contact(db_session, group_id, tenant_id, "Xaver")
    x_offer = _offer(db_session, tenant_id, group_id, x.id)
    d_offer = _offer(db_session, tenant_id, group_id, d.id)

    x_into_d = _merge(db_session, duplicate_id=x.id, survivor_id=d.id, group_id=group_id)
    d_into_t = _merge(db_session, duplicate_id=d.id, survivor_id=t.id, group_id=group_id)

    assert _deliver(db_session, d_into_t) is True
    assert _deliver(db_session, x_into_d) is True

    assert _contact_ids(db_session, x_offer, d_offer) == [t.id, t.id]
    assert db_session.scalars(select(SalesOffer).where(SalesOffer.contact_id.in_([x.id, d.id]))).all() == []
    assert db_session.scalars(select(SalesContract).where(SalesContract.contact_id.in_([x.id, d.id]))).all() == []


def test_the_moved_rows_take_the_survivors_label(db_session):
    """KAN-580: the consumer moved `contact_id` and left `contact_label` —
    the Sales list and the detail screens kept naming the duplicate, a
    contact that no longer exists as a record (rule 2: a label follows its
    owner). The survivor is read from `contact.public`, never from the
    payload. The consumer leaves the correspondence language as it was; on
    open work the nightly `sales.contact_labels` job (KAN-590) then gives it
    the survivor's."""

    tenant_id, group_id = uuid.uuid4(), uuid.uuid4()
    survivor = _contact(db_session, group_id, tenant_id, "Survivor")
    survivor.language = type(survivor.language)("fr")
    db_session.commit()
    duplicate = _contact(db_session, group_id, tenant_id, "Duplicate")
    offer = _offer(db_session, tenant_id, group_id, duplicate.id)
    contracted_offer = _offer(db_session, tenant_id, group_id, duplicate.id)
    contract = create_contract(db_session, tenant_id=tenant_id, offer=contracted_offer, actor_id=uuid.uuid4())
    deals = list(db_session.scalars(select(SalesDeal).where(SalesDeal.contact_id == duplicate.id)).all())
    assert {row.contact_label for row in (offer, contracted_offer, contract, *deals)} == {"Duplicate Keller"}

    message = _merge(db_session, duplicate_id=duplicate.id, survivor_id=survivor.id, group_id=group_id)
    assert _deliver(db_session, message) is True

    db_session.expire_all()
    moved = [db_session.get(type(row), row.id) for row in (offer, contracted_offer, contract, *deals)]
    assert [row.contact_label for row in moved] == ["Survivor Keller"] * 5
    assert all(row.contact_denorm_refreshed_at is not None for row in moved)
    # The consumer leaves the language; on open work the nightly sales.contact_labels
    # job (KAN-590) then gives it the survivor's.
    assert [row.contact_language for row in moved[:3]] == ["de"] * 3


def test_a_survivor_renamed_after_the_merge_event_is_read_as_it_stands_now(db_session):
    """KAN-580: never the payload — the label is the survivor's as the
    consumer finds it (delivery is unordered and may be late)."""

    tenant_id, group_id = uuid.uuid4(), uuid.uuid4()
    survivor = _contact(db_session, group_id, tenant_id, "Survivor")
    duplicate = _contact(db_session, group_id, tenant_id, "Duplicate")
    offer = _offer(db_session, tenant_id, group_id, duplicate.id)
    message = _merge(db_session, duplicate_id=duplicate.id, survivor_id=survivor.id, group_id=group_id)
    survivor = contact_common_service.get_contact_or_404(db_session, group_id, survivor.id)
    survivor.first_name = "Renamed"
    db_session.commit()

    assert _deliver(db_session, message) is True

    db_session.expire_all()
    assert db_session.get(SalesOffer, offer.id).contact_label == "Renamed Keller"
