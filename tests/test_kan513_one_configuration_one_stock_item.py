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
from app.core.outbox_model import OutboxMessage
from app.inventory.consumers import handle_sales_contract_cancelled_message, handle_sales_contract_confirmed_message
from app.inventory.models.stock_item import LifecycleStatus, StockItem, StockItemCondition
from app.inventory.services.configuration_holding import settle_ordered_configuration
from app.sales.consumers import handle_stock_item_added_message
from app.sales.models.contract import SalesContract
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import cancel_contract, confirm_contract, create_contract
from app.sales.services.offer import create_offer, update_offer
from app.vehicle.models.configuration import VehicleConfiguration
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
    assert reassigned.payload["originContractId"] == str(second.id)
    assert reassigned.payload["originRole"] == "manual_configuration"

    # Sales records the reused car on the second contract, as on `added`.
    consume_once(db_session, message=reassigned, consumer_name="sales.stock_item_reassigned", handler=handle_stock_item_added_message)
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
