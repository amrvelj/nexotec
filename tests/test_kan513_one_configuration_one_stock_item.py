"""KAN-513: one configuration, one stock item — for every writer.

The partial unique index `uq_stock_item_tenant_id_configuration_id` refuses a
second stock item of the Betrieb holding a configuration. The FR-I-02/02b
consumer, which keys on the contract, settles a configuration another item
holds under the same advisory lock as create and attach (Anto, 2026-11-03,
option A): a contract cancelled and re-signed on the same offer reuses the
first pipeline car while it is free on stock, and gets a copy of the
configuration once that car has gone elsewhere.

Events are delivered the way production delivers them: the real outbox rows,
through consume_once with the handlers app.worker registers.
"""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.base import utcnow
from app.core.consumer import consume_once
from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.inventory.consumers import handle_sales_contract_cancelled_message, handle_sales_contract_confirmed_message
from app.inventory.models.stock_item import LifecycleStatus, StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services import pipeline as pipeline_service
from app.inventory.services import stock_item as stock_item_service
from app.inventory.services.configuration_holding import Settled, settle_ordered_configuration
from app.sales.consumers import handle_stock_item_reassigned_message
from app.sales.models.contract import SalesContract
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import cancel_contract, confirm_contract, create_contract
from app.sales.services.offer import create_offer, update_offer
from app.vehicle.models.configuration import VehicleConfiguration
from tests.postgres_only import NEEDS_ITS_OWN_CONNECTION, POSTGRES_SQL
from tests.test_configurator_hosts import _build_configuration, _dealership, _session_factory
from tests.test_sales_lifecycle_reservation import _with_buyer


def _messages(db_session, event_type, aggregate_id) -> list[OutboxMessage]:
    return list(
        db_session.scalars(
            select(OutboxMessage)
            .where(OutboxMessage.event_type == event_type, OutboxMessage.aggregate_id == aggregate_id)
            .order_by(OutboxMessage.occurred_at)
        ).all()
    )


def _deliver(db_session, event_type, aggregate_id, consumer_name, handler) -> None:
    (message,) = _messages(db_session, event_type, aggregate_id)
    consume_once(db_session, message=message, consumer_name=consumer_name, handler=handler)


def _confirm(db_session, engine, offer, group_id) -> SalesContract:
    contract = create_contract(db_session, tenant_id=offer.tenant_id, offer=offer, actor_id=uuid.uuid4())
    contract = confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine)
    )
    _deliver(db_session, "sales.contract.confirmed", contract.id, "inventory.sales_contract_confirmed", handle_sales_contract_confirmed_message)
    return contract


def _cancel(db_session, engine, contract) -> None:
    cancel_contract(db_session, contract=contract, reason="Kunde zurückgetreten", actor_id=uuid.uuid4(), session_factory=_session_factory(engine))
    _deliver(db_session, "sales.contract.cancelled", contract.id, "inventory.sales_contract_cancelled", handle_sales_contract_cancelled_message)


def _ordered_car(db_session, contract) -> StockItem | None:
    return db_session.scalars(select(StockItem).where(StockItem.pipeline_ref == f"contract:{contract.id}:manual")).first()


def _first_contract_cancelled(db_session, engine):
    """The offer's configuration went into a pipeline car with the first
    contract; that contract is cancelled, so the car is free on stock."""

    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    group_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = update_offer(db_session, offer=offer, group_id=group_id, data=OfferUpdate(configuration_id=config.id), actor_id=None)
    offer = _with_buyer(db_session, offer, group_id)
    first = _confirm(db_session, engine, offer, group_id)
    car = _ordered_car(db_session, first)
    assert car is not None and car.reserved_by_contract_id == first.id
    _cancel(db_session, engine, first)
    db_session.refresh(car)
    assert car.active_reservation_id is None
    return dealership, config, group_id, offer, car


def test_a_second_contract_on_the_same_offer_reuses_the_free_pipeline_car(db_session, engine):
    _, config, group_id, offer, car = _first_contract_cancelled(db_session, engine)

    second = _confirm(db_session, engine, offer, group_id)

    db_session.refresh(car)
    assert car.reserved_by_contract_id == second.id
    assert _ordered_car(db_session, second) is None  # no second pipeline car
    assert db_session.scalar(select(func.count()).select_from(StockItem).where(StockItem.configuration_id == config.id)) == 1
    (reassigned,) = _messages(db_session, "inventory.stock_item.reassigned", car.id)
    assert reassigned.payload["contractId"] == str(second.id)
    assert reassigned.payload["originRole"] == "manual_configuration"
    assert "originContractId" not in reassigned.payload  # the car's origin stays its first contract's

    # Sales records the reused car on the second contract, as on `added`.
    consume_once(db_session, message=reassigned, consumer_name="sales.stock_item_reassigned", handler=handle_stock_item_reassigned_message)
    db_session.expire_all()
    assert db_session.get(SalesContract, second.id).stock_item_id == car.id
    assert db_session.get(SalesContract, second.id).stock_item_label == car.stock_number

    # The same confirmation handled again (a duplicate emission) changes nothing.
    (confirmation,) = _messages(db_session, "sales.contract.confirmed", second.id)
    handle_sales_contract_confirmed_message(db_session, confirmation)
    db_session.commit()
    assert len(_messages(db_session, "inventory.stock_item.reassigned", car.id)) == 1


def test_a_second_contract_gets_a_copy_once_the_first_car_has_gone_elsewhere(db_session, engine):
    dealership, config, group_id, offer, car = _first_contract_cancelled(db_session, engine)
    car.left_stock_at = utcnow()  # sold elsewhere meanwhile
    db_session.commit()

    second = _confirm(db_session, engine, offer, group_id)

    ordered = _ordered_car(db_session, second)
    assert ordered is not None and ordered.id != car.id
    assert ordered.configuration_id not in (None, config.id)
    assert ordered.reserved_by_contract_id == second.id
    copy = db_session.get(VehicleConfiguration, ordered.configuration_id)
    assert copy is not None and copy.tenant_id == dealership.id
    assert _messages(db_session, "inventory.stock_item.reassigned", car.id) == []


def test_a_trade_ins_configuration_held_by_another_item_is_copied_never_reused(db_session):
    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    first = StockItem(
        tenant_id=dealership.id, stock_number="S-513001", vehicle_label="Trade-in", condition=StockItemCondition.USED,
        lifecycle_status=LifecycleStatus.PIPELINE, configuration_id=config.id, pipeline_ref="contract:x:trade_in",
    )
    db_session.add(first)
    db_session.commit()

    settled = settle_ordered_configuration(
        db_session, tenant_id=dealership.id, configuration_id=config.id, contract_id=uuid.uuid4(), may_reuse=False
    )

    assert settled.reuse is None
    assert settled.configuration_id != config.id
    assert db_session.get(VehicleConfiguration, settled.configuration_id) is not None


def test_the_index_refuses_a_second_item_with_the_configuration(db_session, engine):
    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    for number in ("S-513101", "S-513102"):
        db_session.add(
            StockItem(
                tenant_id=dealership.id, stock_number=number, vehicle_label="x", condition=StockItemCondition.NEW,
                lifecycle_status=LifecycleStatus.PIPELINE, configuration_id=config.id,
            )
        )
    # Postgres names the index (the services map an IntegrityError to the 409
    # by that name); SQLite, the fast lane, names the columns.
    named = "uq_stock_item_tenant_id_configuration_id" if engine.dialect.name == "postgresql" else "configuration_id"
    with pytest.raises(IntegrityError, match=named):
        db_session.commit()
    db_session.rollback()


# --- the index's IntegrityError is the same 409, whichever writer skipped the lock ---------


def _held(db_session):
    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    holder = StockItem(
        tenant_id=dealership.id, stock_number="S-513201", vehicle_label="held", condition=StockItemCondition.NEW,
        lifecycle_status=LifecycleStatus.PIPELINE, configuration_id=config.id,
    )
    db_session.add(holder)
    db_session.commit()
    return dealership, config, holder


def _assert_held_409(exc_info, holder) -> None:
    assert exc_info.value.details == {
        "reason": "configuration_held_by_stock_item", "stockItemId": str(holder.id), "stockNumber": holder.stock_number,
    }


@POSTGRES_SQL
def test_create_maps_the_index_refusal_to_the_409(db_session, monkeypatch):
    dealership, config, holder = _held(db_session)
    monkeypatch.setattr(stock_item_service, "_check_configuration_not_held", lambda db, **kw: None)

    with pytest.raises(ConflictError) as exc_info:
        stock_item_service.create_stock_item(
            db_session, tenant_id=dealership.id, actor_id=None,
            data=StockItemCreate(vehicle_label="x", condition=StockItemCondition.NEW, configuration_id=config.id),
        )
    _assert_held_409(exc_info, holder)


@POSTGRES_SQL
def test_attach_maps_the_index_refusal_to_the_409(db_session, monkeypatch):
    dealership, config, holder = _held(db_session)
    item = StockItem(
        tenant_id=dealership.id, stock_number="S-513202", vehicle_label="bare", condition=StockItemCondition.NEW,
        lifecycle_status=LifecycleStatus.PIPELINE,
    )
    db_session.add(item)
    db_session.commit()
    monkeypatch.setattr(stock_item_service, "_check_configuration_not_held", lambda db, **kw: None)

    with pytest.raises(ConflictError) as exc_info:
        stock_item_service.attach_configuration(db_session, item=item, configuration_id=config.id, actor_id=None)
    _assert_held_409(exc_info, holder)
    db_session.rollback()


@POSTGRES_SQL
def test_the_consumer_maps_the_index_refusal_to_the_409(db_session, engine, monkeypatch):
    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    group_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = update_offer(db_session, offer=offer, group_id=group_id, data=OfferUpdate(configuration_id=config.id), actor_id=None)
    offer = _with_buyer(db_session, offer, group_id)
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    contract = confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine)
    )
    holder = StockItem(  # a writer that skipped the lock got there first
        tenant_id=dealership.id, stock_number="S-513203", vehicle_label="held", condition=StockItemCondition.NEW,
        lifecycle_status=LifecycleStatus.PIPELINE, configuration_id=config.id,
    )
    db_session.add(holder)
    db_session.commit()
    monkeypatch.setattr(
        pipeline_service, "settle_ordered_configuration",
        lambda db, *, configuration_id, **kw: Settled(reuse=None, configuration_id=configuration_id),
    )
    (confirmation,) = _messages(db_session, "sales.contract.confirmed", contract.id)

    with pytest.raises(ConflictError) as exc_info:
        handle_sales_contract_confirmed_message(db_session, confirmation)
    _assert_held_409(exc_info, holder)
    db_session.rollback()


# --- only an ordered car is reused; a failed handler discards its copy -----------------------


def test_a_trade_in_car_holding_the_configuration_is_never_reused_for_an_order(db_session):
    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    trade_in = StockItem(
        tenant_id=dealership.id, stock_number="S-513301", vehicle_label="Trade-in", condition=StockItemCondition.USED,
        lifecycle_status=LifecycleStatus.PIPELINE, configuration_id=config.id, pipeline_ref="contract:x:trade_in",
    )
    db_session.add(trade_in)
    db_session.commit()

    settled = settle_ordered_configuration(
        db_session, tenant_id=dealership.id, configuration_id=config.id, contract_id=uuid.uuid4(), may_reuse=True
    )

    assert settled.reuse is None and settled.copied
    assert settled.configuration_id != config.id


@NEEDS_ITS_OWN_CONNECTION
def test_a_handler_that_fails_after_copying_discards_the_copy(db_session, engine, monkeypatch):
    dealership, _, group_id, offer, car = _first_contract_cancelled(db_session, engine)
    car.left_stock_at = utcnow()
    db_session.commit()
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    contract = confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine)
    )
    configurations_before = db_session.scalar(select(func.count()).select_from(VehicleConfiguration))

    def _fails(*args, **kwargs):
        raise RuntimeError("the item write failed")

    monkeypatch.setattr(pipeline_service, "_create_pipeline_item_idempotent", _fails)
    (confirmation,) = _messages(db_session, "sales.contract.confirmed", contract.id)

    with pytest.raises(RuntimeError):
        handle_sales_contract_confirmed_message(db_session, confirmation)
    db_session.rollback()

    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(VehicleConfiguration)) == configurations_before
