"""KAN-188 / FR-C-11: a copied offer gets a configuration of its own.

`copy_offer` used to copy `configuration_id` as a reference, so reopening the
configurator on the copy edited the source offer's configuration too — and
the pipeline item a contract from the source points at. Vehicle now copies
the configuration on a session of its own (ADR-047) and discards that copy
again when the copy offer's own write fails. The trade-in keeps the
configuration its valuation was judged on (Anto's ruling 2026-10-28,
option A).
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.audit_model import AuditEvent
from app.core.outbox_model import OutboxMessage
from app.sales.models.offer import OfferStatus
from app.sales.schemas.offer import OfferUpdate
from app.sales.services import offer as offer_service
from app.sales.services.offer import copy_offer, create_offer, update_offer
from app.vehicle.models.configuration import VehicleConfiguration, VehicleConfigurationOption
from app.vehicle.schemas.configuration import ConfigurationUpdate
from app.vehicle.schemas.spec_block import VehicleSpecBlockInput
from app.vehicle.services import configuration as configuration_service
from tests.test_configurator_hosts import _bearer, _build_configuration, _dealership


def _configured_open_offer(db_session):
    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    source = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    source = update_offer(
        db_session, offer=source, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id),
        actor_id=uuid.uuid4(),
    )
    source.status = OfferStatus.OPEN
    db_session.commit()
    return dealership, config, source


def _configuration_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(VehicleConfiguration))


def test_the_copy_gets_its_own_configuration_with_the_same_content(db_session):
    _dealership_row, config, source = _configured_open_offer(db_session)

    copy = copy_offer(db_session, source=source, actor_id=uuid.uuid4())

    assert copy.configuration_id is not None
    assert copy.configuration_id != source.configuration_id == config.id
    assert copy.configuration_label == source.configuration_label == "Volkswagen ID.4 Pro Performance"
    assert copy.vehicle_snapshot["configurationId"] == str(copy.configuration_id)
    copied = db_session.get(VehicleConfiguration, copy.configuration_id)
    assert copied.tenant_id == config.tenant_id
    assert copied.catalogue_variant_id == config.catalogue_variant_id
    assert [(o.option_code, o.price, o.selected) for o in copied.options] == [
        (o.option_code, o.price, o.selected) for o in config.options
    ]
    # The copy prices exactly like its source: same options, same surcharges.
    assert copy.options_total == source.options_total == Decimal("3730.00")
    assert copy.list_price == source.list_price


def test_editing_the_copys_configuration_leaves_the_source_configuration_untouched(db_session):
    """The ticket's exit criterion: an edit on the copy never reaches the source."""

    _dealership_row, config, source = _configured_open_offer(db_session)
    source_ps = config.ps
    copy = copy_offer(db_session, source=source, actor_id=uuid.uuid4())

    copied = configuration_service.get_configuration_or_404(
        db_session, tenant_id=copy.tenant_id, configuration_id=copy.configuration_id
    )
    configuration_service.update_configuration(
        db_session, configuration=copied, actor_id=uuid.uuid4(), data=ConfigurationUpdate(spec=VehicleSpecBlockInput(ps=286)),
    )
    configuration_service.replace_options(db_session, configuration=copied, actor_id=uuid.uuid4(), options=[])
    # The draft copy picks its own new version up …
    copy = update_offer(
        db_session, offer=copy, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=copy.configuration_id),
        actor_id=None,
    )
    assert copy.vehicle_snapshot["spec"]["ps"] == 286

    # … and the source's configuration is exactly as it was.
    db_session.expire_all()
    original = db_session.get(VehicleConfiguration, config.id)
    assert original.ps == source_ps != 286
    assert original.version == config.version
    assert {o.option_code for o in original.options} == {"HP1", "PD2", "XX9", "SR0"}


def test_the_trade_in_keeps_the_configuration_its_valuation_was_judged_on(db_session):
    """Anto's ruling 2026-10-28 (option A): only the car being sold is copied."""

    dealership, _config, source = _configured_open_offer(db_session)
    trade_in_configuration_id = uuid.uuid4()
    trade_in_valuation_id = uuid.uuid4()
    source.trade_in_configuration_id = trade_in_configuration_id
    source.trade_in_valuation_id = trade_in_valuation_id
    db_session.commit()
    before = _configuration_count(db_session)

    copy = copy_offer(db_session, source=source, actor_id=uuid.uuid4())

    assert copy.trade_in_configuration_id == trade_in_configuration_id
    assert copy.trade_in_valuation_id == trade_in_valuation_id
    assert _configuration_count(db_session) == before + 1  # the sold car's copy only


def test_an_offer_without_a_configuration_copies_no_configuration(db_session):
    dealership = _dealership(db_session)
    source = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    source = update_offer(
        db_session, offer=source, group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="manual", manual_vehicle_condition="used", manual_base_price="28900.00"),
        actor_id=uuid.uuid4(),
    )

    copy = copy_offer(db_session, source=source, actor_id=uuid.uuid4())

    assert copy.configuration_id is None
    assert _configuration_count(db_session) == 0


def test_a_failed_offer_copy_discards_the_configuration_it_copied(db_session, monkeypatch):
    """ADR-047: the configuration copy commits on vehicle's own session, so
    the compensation removes it when the copy offer's write fails — no
    configuration nobody references is left behind."""

    _dealership_row, config, source = _configured_open_offer(db_session)
    before = _configuration_count(db_session)
    options_before = db_session.scalar(select(func.count()).select_from(VehicleConfigurationOption))

    def _fail(*_args, **_kwargs):
        raise RuntimeError("deal projection down")

    monkeypatch.setattr(offer_service, "upsert_deal_projection", _fail)
    with pytest.raises(RuntimeError, match="deal projection down"):
        copy_offer(db_session, source=source, actor_id=uuid.uuid4())

    db_session.expire_all()
    assert _configuration_count(db_session) == before
    assert db_session.scalar(select(func.count()).select_from(VehicleConfigurationOption)) == options_before
    assert db_session.get(VehicleConfiguration, config.id) is not None  # the source's own is untouched
    discarded = db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == "vehicle.configuration.discarded")
    ).all()
    assert len(discarded) == 1
    assert db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_id == discarded[0].aggregate_id, AuditEvent.action == "delete"
        )
    ).one().reason == "host_copy_compensated"


def test_copy_offer_api_returns_the_copys_own_configuration(client, db_session):
    dealership, config, source = _configured_open_offer(db_session)

    response = client.post(
        f"/v1/sales/offers/{source.id}/copy", headers={**_bearer(dealership.id), "Idempotency-Key": str(uuid.uuid4())}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["configurationId"] not in (None, str(config.id))
    assert body["configurationLabel"] == "Volkswagen ID.4 Pro Performance"
