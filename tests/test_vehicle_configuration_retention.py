"""KAN-270 (Anto, 2026-11-03: option A, then C) — a configuration no host
ever took is deleted 30 days after it was created; one a host took and let go
is kept for good.

Vehicle learns that a host took one from the host's own past-tense fact,
delivered here the way production delivers it: the real outbox row, through
`consume_once` with the handler and consumer name `app.worker` registers. The
daily job runs as the worker runs it (`run_daily_unattached_configuration_purge`,
with every host context's read-only `referenced_configuration_ids`).
"""

import datetime as dt
import logging
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.audit_model import AuditEvent
from app.core.base import utcnow
from app.core.consumer import consume_once
from app.core.outbox import OutboxEvent, publish
from app.core.outbox_model import OutboxMessage
from app.inventory.models.stock_item import StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.stock_item import attach_configuration, create_stock_item
from app.sales.models.contract import SalesContract
from app.sales.models.offer import SalesOffer
from app.sales.schemas.contract import ContractUpdate
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import create_contract, update_contract
from app.sales.services.offer import copy_offer, create_offer, update_offer
from app.sales.services.trade_in import attach_trade_in_valuation
from app.valuation.models.valuation import Valuation, ValuationSource
from app.valuation.schemas.valuation import ValuationCreate
from app.valuation.services.valuation import create_valuation
from app.vehicle import daily_jobs
from app.vehicle.consumers import handle_configuration_taken_message
from app.vehicle.daily_jobs import run_daily_unattached_configuration_purge
from app.vehicle.models.configuration import VehicleConfiguration, VehicleConfigurationOption
from app.vehicle.services.configuration_retention import purge_unattached_configurations
from app.worker import register_daily_jobs
from tests.test_configurator_hosts import _build_configuration, _dealership, _record_configuration
from tests.test_sales_contract_edit import _customer_born_contract

# event type -> the consumer app.worker registers for it
CONSUMERS = {
    "sales.offer.configuration_attached": "vehicle.configuration_taken_by_offer",
    "sales.contract.configuration_attached": "vehicle.configuration_taken_by_contract",
    "inventory.stock_item.added": "vehicle.configuration_taken_by_added_stock_item",
    "inventory.stock_item.configuration_attached": "vehicle.configuration_taken_by_stock_item",
    "valuation.created": "vehicle.configuration_taken_by_valuation",
}


# --- helpers ------------------------------------------------------------------


def _messages(db: Session, event_type: str, aggregate_id: uuid.UUID) -> list[OutboxMessage]:
    return list(
        db.scalars(
            select(OutboxMessage)
            .where(OutboxMessage.event_type == event_type, OutboxMessage.aggregate_id == aggregate_id)
            .order_by(OutboxMessage.occurred_at)
        )
    )


def _deliver(db: Session, event_type: str, aggregate_id: uuid.UUID) -> None:
    messages = _messages(db, event_type, aggregate_id)
    assert messages, f"{event_type} was not published for {aggregate_id}"
    for message in messages:
        consume_once(db, message=message, consumer_name=CONSUMERS[event_type], handler=handle_configuration_taken_message)


def _config(db: Session, configuration_id: uuid.UUID) -> VehicleConfiguration | None:
    db.expire_all()
    return db.get(VehicleConfiguration, configuration_id)


def _age(db: Session, configuration_id: uuid.UUID, *, days: int) -> None:
    db.execute(
        update(VehicleConfiguration)
        .where(VehicleConfiguration.id == configuration_id)
        .values(created_at=utcnow() - dt.timedelta(days=days), updated_at=VehicleConfiguration.updated_at)
    )
    db.commit()


def _discarded(db: Session, configuration_id: uuid.UUID) -> int:
    return db.scalar(
        select(func.count())
        .select_from(OutboxMessage)
        .where(
            OutboxMessage.event_type == "vehicle.configuration.discarded",
            OutboxMessage.aggregate_id == configuration_id,
        )
    )


def _purge_audits(db: Session, configuration_id: uuid.UUID) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type == "vehicle_configuration",
                AuditEvent.entity_id == configuration_id,
                AuditEvent.reason == "never_taken_purged",
            )
        )
    )


def _valuation(db: Session, tenant_id: uuid.UUID, configuration_id: uuid.UUID) -> Valuation:
    return create_valuation(
        db, tenant_id=tenant_id, group_id=uuid.uuid4(), actor_id=uuid.uuid4(),
        data=ValuationCreate(configuration_id=configuration_id, source=ValuationSource.MANUAL, final_offer=Decimal("2500.00")),
    )


def _pipeline_item(db: Session, tenant_id: uuid.UUID, configuration_id: uuid.UUID | None) -> StockItem:
    return create_stock_item(
        db, tenant_id=tenant_id, actor_id=None,
        data=StockItemCreate(
            vehicle_label="Subaru Justy", condition=StockItemCondition.USED, configuration_id=configuration_id
        ),
    )


# --- how each host takes a configuration, and lets it go --------------------
#
# Each returns (configuration id, release). `release` makes the host stop
# naming the configuration: Sales' own edit where one exists (Path A, or the
# configuration cleared); for Stock and Valuation no production path lets a
# configuration go today (Stock refuses to replace one, valuations are never
# deleted), so the test clears the column — the case a future path, or a
# deleted host row, would make.


def _taken_by_offer(db, dealership):
    config = _build_configuration(db, dealership.id)
    offer = create_offer(db, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = update_offer(db, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id), actor_id=None)
    _deliver(db, "sales.offer.configuration_attached", offer.id)

    def release():
        update_offer(db, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=None), actor_id=None)

    return config.id, release


def _taken_by_contract(db, dealership):
    config = _build_configuration(db, dealership.id)
    contract = _customer_born_contract(db, dealership.id, uuid.uuid4())
    contract = update_contract(
        db, contract=contract, group_id=uuid.uuid4(), data=ContractUpdate(configuration_id=config.id), actor_id=None
    )
    _deliver(db, "sales.contract.configuration_attached", contract.id)

    def release():
        update_contract(
            db, contract=contract, group_id=uuid.uuid4(), data=ContractUpdate(configuration_id=None), actor_id=None
        )

    return config.id, release


def _clear(db, model, column, configuration_id):
    db.execute(update(model).where(column == configuration_id).values({column.key: None}))
    db.commit()


def _taken_by_added_stock_item(db, dealership):
    config = _record_configuration(db, dealership.id)
    item = _pipeline_item(db, dealership.id, config.id)
    _deliver(db, "inventory.stock_item.added", item.id)
    return config.id, lambda: _clear(db, StockItem, StockItem.configuration_id, config.id)


def _taken_by_stock_item(db, dealership):
    config = _record_configuration(db, dealership.id)
    item = _pipeline_item(db, dealership.id, None)
    attach_configuration(db, item=item, configuration_id=config.id, actor_id=uuid.uuid4())
    _deliver(db, "inventory.stock_item.configuration_attached", item.id)
    return config.id, lambda: _clear(db, StockItem, StockItem.configuration_id, config.id)


def _taken_by_valuation(db, dealership):
    config = _record_configuration(db, dealership.id)
    valuation = _valuation(db, dealership.id, config.id)
    _deliver(db, "valuation.created", valuation.id)
    return config.id, lambda: _clear(db, Valuation, Valuation.configuration_id, config.id)


HOSTS = {
    "offer": _taken_by_offer,
    "contract": _taken_by_contract,
    "added_stock_item": _taken_by_added_stock_item,
    "stock_item_attach": _taken_by_stock_item,
    "valuation": _taken_by_valuation,
}


# --- C: taken once, kept for good ----------------------------------------------


@pytest.mark.parametrize("host", sorted(HOSTS))
def test_a_configuration_a_host_took_survives_its_release_past_30_days(db_session, host):
    dealership = _dealership(db_session)
    configuration_id, release = HOSTS[host](db_session, dealership)
    stamped = _config(db_session, configuration_id).first_taken_at
    assert stamped is not None, "the host's fact did not stamp the configuration taken"

    release()
    _age(db_session, configuration_id, days=45)
    run_daily_unattached_configuration_purge(db_session)

    kept = _config(db_session, configuration_id)
    assert kept is not None, "a configuration a host took and let go was deleted"
    assert kept.first_taken_at == stamped, "the stamp moved"
    assert _discarded(db_session, configuration_id) == 0
    assert _purge_audits(db_session, configuration_id) == []


def test_the_stamp_is_the_hosts_moment_and_leaves_version_and_updated_at_alone(db_session):
    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    version, updated_at = config.version, config.updated_at
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    update_offer(db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id), actor_id=None)
    [message] = _messages(db_session, "sales.offer.configuration_attached", offer.id)
    assert message.payload == {"offerNumber": offer.offer_number, "configurationId": str(config.id), "slot": "vehicle"}

    _deliver(db_session, "sales.offer.configuration_attached", offer.id)

    stamped = _config(db_session, config.id)
    assert stamped.first_taken_at == message.occurred_at
    assert (stamped.version, stamped.updated_at) == (version, updated_at)


def test_reattaching_the_same_configuration_publishes_no_second_fact(db_session):
    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    for _ in range(2):
        offer = update_offer(
            db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id), actor_id=None
        )

    assert len(_messages(db_session, "sales.offer.configuration_attached", offer.id)) == 1


def test_a_trade_in_and_a_contract_from_the_offer_and_a_copy_each_publish_what_they_hold(db_session):
    dealership = _dealership(db_session)
    sold = _build_configuration(db_session, dealership.id)
    traded = _record_configuration(db_session, dealership.id)
    valuation = _valuation(db_session, dealership.id, traded.id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = update_offer(db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=sold.id), actor_id=None)
    offer = attach_trade_in_valuation(db_session, deal=offer, valuation_id=valuation.id, actor_id=None)

    def held(event_type, aggregate_id):
        return {(m.payload["slot"], m.payload["configurationId"]) for m in _messages(db_session, event_type, aggregate_id)}

    assert held("sales.offer.configuration_attached", offer.id) == {("vehicle", str(sold.id)), ("trade_in", str(traded.id))}

    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    assert held("sales.contract.configuration_attached", contract.id) == {
        ("vehicle", str(sold.id)), ("trade_in", str(traded.id)),
    }

    source = db_session.get(SalesOffer, offer.id)
    copy = copy_offer(db_session, source=source, actor_id=uuid.uuid4())
    assert copy.configuration_id not in (None, sold.id)  # KAN-188: a copy of its own
    assert held("sales.offer.configuration_attached", copy.id) == {
        ("vehicle", str(copy.configuration_id)), ("trade_in", str(traded.id)),
    }

    # The trade-in fact alone stamps the trade-in configuration.
    _deliver(db_session, "sales.contract.configuration_attached", contract.id)
    assert _config(db_session, traded.id).first_taken_at is not None
    assert db_session.get(SalesContract, contract.id).trade_in_configuration_id == traded.id


def test_the_stock_and_valuation_facts_carry_the_configuration_id(db_session):
    dealership = _dealership(db_session)
    without = _pipeline_item(db_session, dealership.id, None)
    config = _record_configuration(db_session, dealership.id)
    with_one = _pipeline_item(db_session, dealership.id, config.id)
    [added_without] = _messages(db_session, "inventory.stock_item.added", without.id)
    [added_with] = _messages(db_session, "inventory.stock_item.added", with_one.id)
    assert "configurationId" not in added_without.payload
    assert added_with.payload["configurationId"] == str(config.id)

    linked = _record_configuration(db_session, dealership.id)
    attach_configuration(db_session, item=without, configuration_id=linked.id, actor_id=uuid.uuid4())
    [attached] = _messages(db_session, "inventory.stock_item.configuration_attached", without.id)
    assert attached.payload == {"stockNumber": without.stock_number, "configurationId": str(linked.id)}

    valuation = _valuation(db_session, dealership.id, config.id)
    [created] = _messages(db_session, "valuation.created", valuation.id)
    assert created.payload["configurationId"] == str(config.id)


def test_a_fact_without_a_configuration_or_from_another_betrieb_stamps_nothing(db_session):
    dealership = _dealership(db_session)
    config = _record_configuration(db_session, dealership.id)
    item = _pipeline_item(db_session, dealership.id, None)
    _deliver(db_session, "inventory.stock_item.added", item.id)  # no configurationId

    foreign = publish(
        db_session,
        OutboxEvent(
            event_type="valuation.created", tenant_id=uuid.uuid4(), producer="valuation", aggregate_type="valuation",
            aggregate_id=uuid.uuid4(), payload={"valuationNumber": "B-1", "configurationId": str(config.id)},
        ),
    )
    db_session.commit()
    _deliver(db_session, "valuation.created", foreign.aggregate_id)

    assert _config(db_session, config.id).first_taken_at is None


# --- A: never taken, deleted 30 days after it was created ----------------------


def test_a_never_taken_configuration_past_30_days_is_deleted_with_an_audit_row_and_an_event(db_session, caplog):
    dealership = _dealership(db_session)
    config = _record_configuration(db_session, dealership.id, vin="JF1GD2DA03G000001")
    built = _build_configuration(db_session, dealership.id)  # with four options
    assert db_session.scalar(
        select(func.count()).select_from(VehicleConfigurationOption).where(VehicleConfigurationOption.configuration_id == built.id)
    ) == 4
    for configuration_id in (config.id, built.id):
        _age(db_session, configuration_id, days=31)

    with caplog.at_level(logging.INFO, logger=daily_jobs.__name__):
        run_daily_unattached_configuration_purge(db_session)

    for configuration_id in (config.id, built.id):
        assert _config(db_session, configuration_id) is None
        assert _discarded(db_session, configuration_id) == 1
        [audit] = _purge_audits(db_session, configuration_id)
        assert audit.action == "delete" and audit.actor_id is None
        assert audit.tenant_id == dealership.id
        assert set(audit.before) == {"source", "mode", "createdAt"}, "the audit row must not keep the VIN or mileage"
    assert db_session.scalar(
        select(func.count()).select_from(VehicleConfigurationOption).where(VehicleConfigurationOption.configuration_id == built.id)
    ) == 0
    [record] = [r for r in caplog.records if r.name == daily_jobs.__name__]
    assert record.getMessage() == "vehicle.configurations.purge_unattached: purged=2 stamped=0"


def test_a_configuration_under_30_days_stays_untouched(db_session):
    dealership = _dealership(db_session)
    config = _record_configuration(db_session, dealership.id)
    _age(db_session, config.id, days=29)

    run_daily_unattached_configuration_purge(db_session)

    kept = _config(db_session, config.id)
    assert kept is not None and kept.first_taken_at is None
    assert _discarded(db_session, config.id) == 0


# --- the safety net: a host holds it, its fact not consumed yet ----------------


def _held_by_offer(db, dealership):
    config = _build_configuration(db, dealership.id)
    offer = create_offer(db, tenant_id=dealership.id, actor_id=uuid.uuid4())
    update_offer(db, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id), actor_id=None)
    return config.id


def _held_by_contract_trade_in(db, dealership):
    traded = _record_configuration(db, dealership.id)
    valuation = _valuation(db, dealership.id, traded.id)
    contract = _customer_born_contract(db, dealership.id, uuid.uuid4())
    attach_trade_in_valuation(db, deal=contract, valuation_id=valuation.id, actor_id=None)
    # Only the contract names it: the valuation's reference is the one rule 2
    # leaves to Valuation, cleared here to isolate Sales' answer.
    _clear(db, Valuation, Valuation.configuration_id, traded.id)
    return traded.id


def _held_by_stock_item(db, dealership):
    config = _record_configuration(db, dealership.id)
    _pipeline_item(db, dealership.id, config.id)
    return config.id


def _held_by_valuation(db, dealership):
    config = _record_configuration(db, dealership.id)
    _valuation(db, dealership.id, config.id)
    return config.id


def _linked_to_a_vehicle(db, dealership):
    """KAN-567 (Anto, 2026-11-05, option A): a configuration whose VIN linked
    it to a car in the register is held by that record for good — no offer,
    contract, stock car or valuation needs to take it."""

    config = _record_configuration(db, dealership.id)
    config.vehicle_id = uuid.uuid4()  # a VIN that resolved to a car in the register (ADR-070)
    db.commit()
    return config.id


SAFETY_NET = {
    "offer": _held_by_offer,
    "contract_trade_in": _held_by_contract_trade_in,
    "stock_item": _held_by_stock_item,
    "valuation": _held_by_valuation,
    "vehicle_record": _linked_to_a_vehicle,
}


@pytest.mark.parametrize("host", sorted(SAFETY_NET))
def test_a_held_but_unstamped_configuration_is_stamped_and_survives(db_session, host):
    dealership = _dealership(db_session)
    configuration_id = SAFETY_NET[host](db_session, dealership)
    assert _config(db_session, configuration_id).first_taken_at is None  # its fact is not consumed
    _age(db_session, configuration_id, days=31)

    run_daily_unattached_configuration_purge(db_session)

    kept = _config(db_session, configuration_id)
    assert kept is not None and kept.first_taken_at is not None
    assert _discarded(db_session, configuration_id) == 0


def test_a_configuration_only_a_known_car_links_is_kept_for_good(db_session):
    """KAN-567 (Anto, 2026-11-05, option A; ADR-120): no offer, contract,
    stock car or valuation ever takes it — the car in the register holds it,
    and every later run keeps it, the stamp unchanged."""

    dealership = _dealership(db_session)
    configuration_id = _linked_to_a_vehicle(db_session, dealership)
    _age(db_session, configuration_id, days=31)
    run_daily_unattached_configuration_purge(db_session)
    stamped_at = _config(db_session, configuration_id).first_taken_at
    assert stamped_at is not None

    _age(db_session, configuration_id, days=400)
    run_daily_unattached_configuration_purge(db_session)

    kept = _config(db_session, configuration_id)
    assert kept is not None and kept.first_taken_at == stamped_at
    assert _discarded(db_session, configuration_id) == 0


def test_a_young_held_configuration_is_stamped_too_so_a_later_release_keeps_it(db_session):
    """The first run after the release stamps every configuration a host
    holds at that moment, whatever its age."""

    dealership = _dealership(db_session)
    configuration_id = _held_by_stock_item(db_session, dealership)
    _age(db_session, configuration_id, days=3)

    run_daily_unattached_configuration_purge(db_session)
    assert _config(db_session, configuration_id).first_taken_at is not None

    _clear(db_session, StockItem, StockItem.configuration_id, configuration_id)
    _age(db_session, configuration_id, days=40)
    run_daily_unattached_configuration_purge(db_session)
    assert _config(db_session, configuration_id) is not None


# --- runs ----------------------------------------------------------------------


def test_a_repeat_run_is_a_no_op(db_session, caplog):
    dealership = _dealership(db_session)
    doomed = _record_configuration(db_session, dealership.id)
    held = _held_by_valuation(db_session, dealership)
    for configuration_id in (doomed.id, held):
        _age(db_session, configuration_id, days=60)
    run_daily_unattached_configuration_purge(db_session)
    outbox, audits = db_session.scalar(select(func.count()).select_from(OutboxMessage)), db_session.scalar(
        select(func.count()).select_from(AuditEvent)
    )
    stamp = _config(db_session, held).first_taken_at
    caplog.clear()

    with caplog.at_level(logging.INFO, logger=daily_jobs.__name__):
        run_daily_unattached_configuration_purge(db_session)

    [record] = [r for r in caplog.records if r.name == daily_jobs.__name__]
    assert record.getMessage() == "vehicle.configurations.purge_unattached: purged=0 stamped=0"
    assert db_session.scalar(select(func.count()).select_from(OutboxMessage)) == outbox
    assert db_session.scalar(select(func.count()).select_from(AuditEvent)) == audits
    assert _config(db_session, held).first_taken_at == stamp


def test_batches_walk_every_unstamped_configuration_once(db_session):
    dealership = _dealership(db_session)
    doomed = [_record_configuration(db_session, dealership.id).id for _ in range(3)]
    held = [_held_by_valuation(db_session, dealership) for _ in range(2)]
    young = _record_configuration(db_session, dealership.id).id
    for configuration_id in [*doomed, *held]:
        _age(db_session, configuration_id, days=31)
    from app.inventory.public import referenced_configuration_ids as stock
    from app.sales.public import referenced_configuration_ids as sales
    from app.valuation.public import referenced_configuration_ids as valuations

    result = purge_unattached_configurations(db_session, held_by_hosts=(sales, stock, valuations), batch_size=2)

    assert (result.purged, result.stamped) == (3, 2)
    assert all(_config(db_session, configuration_id) is None for configuration_id in doomed)
    assert all(_config(db_session, configuration_id).first_taken_at is not None for configuration_id in held)
    assert _config(db_session, young).first_taken_at is None


def test_the_job_is_registered_last():
    from app.core import daily_scheduler

    saved = dict(daily_scheduler._REGISTRY)
    daily_scheduler._REGISTRY.clear()
    try:
        register_daily_jobs()
        names = daily_scheduler.registered_job_names()
    finally:
        daily_scheduler._REGISTRY.clear()
        daily_scheduler._REGISTRY.update(saved)
    assert names[-1] == "vehicle.configurations.purge_unattached"
