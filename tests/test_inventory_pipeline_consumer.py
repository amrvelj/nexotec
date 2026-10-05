"""WP-7 PR-2: pipeline vehicles + promotion (ADR-045).

sales.contract.confirmed doesn't exist anywhere in app.sales yet (see
app/inventory/services/pipeline.py's own docstring) — every event here is
a directly-constructed synthetic OutboxMessage, proving the mechanism
exactly as tests/test_customer_outbox_idempotency.py does for
customer.created.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.base import utcnow
from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.core.outbox_transport import InProcessTransport
from app.core.outbox_worker import poll_once
from app.core.processed_event_model import ProcessedEvent
from app.core.uuid7 import uuid7
from app.inventory.consumers import handle_sales_contract_confirmed_message
from app.inventory.models.stock_item import LifecycleStatus, StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.pipeline import handle_sales_contract_confirmed, promote_to_vehicle_mdm
from app.inventory.services.stock_item import create_stock_item

_CONSUMER_NAME = "inventory.sales_contract_confirmed"


def _make_message(db_session, *, tenant_id: uuid.UUID, payload: dict) -> OutboxMessage:
    message = OutboxMessage(
        id=uuid7(),
        event_type="sales.contract.confirmed",
        event_version=1,
        occurred_at=utcnow(),
        tenant_id=tenant_id,
        producer="sales",
        aggregate_type="contract",
        aggregate_id=uuid7(),
        correlation_id=uuid7(),
        causation_id=None,
        payload=payload,
        status=OutboxStatus.PENDING,
        attempts=0,
        next_attempt_at=utcnow(),
    )
    db_session.add(message)
    db_session.flush()
    return message


def test_manual_configuration_creates_a_pipeline_item(db_session):
    tenant_id = uuid.uuid4()
    contract_id = uuid.uuid4()
    handle_sales_contract_confirmed(
        db_session,
        tenant_id=tenant_id,
        payload={
            "contractId": str(contract_id),
            "vehicleSource": "manual",
            "manualConfiguration": {"vehicleLabel": "Škoda Octavia Combi", "condition": "new"},
        },
    )
    db_session.commit()

    item = db_session.scalar(select(StockItem).where(StockItem.tenant_id == tenant_id))
    assert item is not None
    assert item.lifecycle_status == LifecycleStatus.PIPELINE
    assert item.vehicle_id is None
    assert item.pipeline_ref == f"contract:{contract_id}:manual"


def test_trade_in_creates_a_separate_pipeline_item_alongside_manual_configuration(db_session):
    tenant_id = uuid.uuid4()
    contract_id = uuid.uuid4()
    handle_sales_contract_confirmed(
        db_session,
        tenant_id=tenant_id,
        payload={
            "contractId": str(contract_id),
            "vehicleSource": "manual",
            "manualConfiguration": {"vehicleLabel": "Škoda Octavia Combi", "condition": "new"},
            "tradeIn": {"vehicleLabel": "Volkswagen Golf", "condition": "used"},
        },
    )
    db_session.commit()

    items = list(db_session.scalars(select(StockItem).where(StockItem.tenant_id == tenant_id)).all())
    assert len(items) == 2
    refs = {i.pipeline_ref for i in items}
    assert refs == {f"contract:{contract_id}:manual", f"contract:{contract_id}:trade_in"}


def test_a_different_message_id_with_the_same_contract_does_not_double_create(db_session, engine):
    """The real defense: not the SAME message redelivered (ProcessedEvent
    already covers that, proven below) but a genuinely duplicate emission
    under a different eventId — caught by the (tenant_id, pipeline_ref)
    unique index.
    """

    tenant_id = uuid.uuid4()
    contract_id = uuid.uuid4()
    payload = {
        "contractId": str(contract_id),
        "vehicleSource": "manual",
        "manualConfiguration": {"vehicleLabel": "Škoda Octavia Combi", "condition": "new"},
    }
    handle_sales_contract_confirmed(db_session, tenant_id=tenant_id, payload=payload)
    db_session.commit()
    handle_sales_contract_confirmed(db_session, tenant_id=tenant_id, payload=payload)
    db_session.commit()

    items = list(db_session.scalars(select(StockItem).where(StockItem.tenant_id == tenant_id)).all())
    assert len(items) == 1


def test_consumer_is_not_reprocessed_on_message_redelivery(db_session, engine):
    tenant_id = uuid.uuid4()
    contract_id = uuid.uuid4()
    message = _make_message(
        db_session,
        tenant_id=tenant_id,
        payload={
            "contractId": str(contract_id),
            "vehicleSource": "manual",
            "manualConfiguration": {"vehicleLabel": "Škoda Octavia Combi", "condition": "new"},
        },
    )
    db_session.commit()
    message_id = message.id

    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    transport = InProcessTransport(session_factory)
    calls: list[uuid.UUID] = []

    def counting_handler(db, msg):
        calls.append(msg.id)
        handle_sales_contract_confirmed_message(db, msg)

    transport.register("sales.contract.confirmed", consumer_name=_CONSUMER_NAME, handler=counting_handler)

    result = poll_once(db_session, transport)
    assert result.published == 1
    assert calls == [message_id]
    assert db_session.get(ProcessedEvent, (message_id, _CONSUMER_NAME)) is not None

    db_session.expire_all()
    redelivered = db_session.get(OutboxMessage, message_id)
    transport.deliver(redelivered)

    assert calls == [message_id], "handler ran a second time on redelivery — idempotency is broken"
    items = list(db_session.scalars(select(StockItem).where(StockItem.tenant_id == tenant_id)).all())
    assert len(items) == 1, "a second stock item was created on redelivery"


def test_promote_to_vehicle_mdm_sets_in_stock_and_denormalized_vin(db_session):
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia Combi", condition=StockItemCondition.NEW),
        actor_id=uuid.uuid4(),
    )
    assert item.lifecycle_status == LifecycleStatus.PIPELINE

    promoted = promote_to_vehicle_mdm(db_session, item=item, vin="1HGCM82633A004352")
    assert promoted.lifecycle_status == LifecycleStatus.IN_STOCK
    assert promoted.vin == "1HGCM82633A004352"
    assert promoted.vehicle_id is not None
    assert promoted.in_stock_at is not None

    message = db_session.scalar(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "inventory.pipeline_vehicle.vin_assigned",
            OutboxMessage.aggregate_id == item.id,
        )
    )
    assert message is not None


def test_promote_to_vehicle_mdm_is_idempotent_and_does_not_emit_twice(db_session):
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia Combi", condition=StockItemCondition.NEW),
        actor_id=uuid.uuid4(),
    )
    promote_to_vehicle_mdm(db_session, item=item, vin="1HGCM82633A004352")
    promote_to_vehicle_mdm(db_session, item=item, vin="1HGCM82633A004352")  # e.g. a retried admin action

    messages = list(
        db_session.scalars(
            select(OutboxMessage).where(
                OutboxMessage.event_type == "inventory.pipeline_vehicle.vin_assigned",
                OutboxMessage.aggregate_id == item.id,
            )
        )
    )
    assert len(messages) == 1


def test_promote_requires_pipeline_lifecycle(db_session):
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(
            vehicle_label="Škoda Octavia Combi",
            condition=StockItemCondition.USED,
            vin="1HGCM82633A004352",
        ),
        actor_id=uuid.uuid4(),
    )
    assert item.lifecycle_status == LifecycleStatus.IN_STOCK
    with pytest.raises(ConflictError):
        promote_to_vehicle_mdm(db_session, item=item, vin="WVWUWDJ62012T0KD3")


# --- KAN-144: the pipeline item names the contract that created it ---------


def _added_payload(db_session, item_id):
    return db_session.scalar(
        select(OutboxMessage.payload).where(
            OutboxMessage.event_type == "inventory.stock_item.added", OutboxMessage.aggregate_id == item_id
        )
    )


def test_a_contract_created_pipeline_item_names_its_origin_on_the_added_event(db_session):
    """Additive fields on inventory.stock_item.added (Anto, 2026-10-04): Sales
    is told which contract a pipeline item came from, so it can learn the
    item's purchase."""

    tenant_id = uuid.uuid4()
    contract_id = uuid.uuid4()
    handle_sales_contract_confirmed(
        db_session,
        tenant_id=tenant_id,
        payload={
            "contractId": str(contract_id),
            "vehicleSource": "manual",
            "manualConfiguration": {"vehicleLabel": "Škoda Octavia Combi", "condition": "new"},
            "tradeIn": {"vehicleLabel": "VW Golf", "condition": "used"},
        },
    )
    db_session.commit()

    items = {i.pipeline_ref: i for i in db_session.scalars(select(StockItem).where(StockItem.tenant_id == tenant_id))}
    manual = _added_payload(db_session, items[f"contract:{contract_id}:manual"].id)
    trade_in = _added_payload(db_session, items[f"contract:{contract_id}:trade_in"].id)
    assert manual["originContractId"] == str(contract_id)
    assert manual["originRole"] == "manual_configuration"
    assert manual["stockNumber"] and manual["vehicleLabel"] == "Škoda Octavia Combi"
    assert trade_in["originContractId"] == str(contract_id)
    assert trade_in["originRole"] == "trade_in"


def test_a_stock_item_added_directly_carries_no_origin(db_session):
    item = create_stock_item(
        db_session, tenant_id=uuid.uuid4(),
        data=StockItemCreate(vehicle_label="Seat Leon", condition=StockItemCondition.USED, vin="1HGCM82633A004352"),
        actor_id=uuid.uuid4(),
    )
    assert set(_added_payload(db_session, item.id)) == {"stockNumber", "vehicleLabel"}
