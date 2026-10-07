"""Configurator C-F (KAN-10) — the host integration, FR-C-12 … FR-C-16.

The mode matrix (PRD v1.4) is asserted at the API of each host, not only in
the overlay: offer Path B `build` only, valuation `record` only, the stock
pipeline both. Contract confirmation and the trade-in run the real outbox
rows through the consumers app.worker registers.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from pydantic.alias_generators import to_camel
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.auth import AccessRole, create_access_token
from app.core.consumer import consume_once
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.i18n import SwissLanguage
from app.core.outbox_model import OutboxMessage
from app.inventory.consumers import handle_sales_contract_confirmed_message
from app.inventory.models.stock_item import LifecycleStatus, StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.stock_item import create_stock_item, list_stock_items
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.platform.schemas.document_content import LineItemsBlock
from app.sales.models.line_item import LineItemKind, SalesLineItem
from app.sales.reconciliation import CHECKS as SALES_CHECKS
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import confirm_contract, create_contract
from app.sales.services.document import _included_option_lines, build_offer_content
from app.sales.services.offer import create_offer, update_offer
from app.sales.services.trade_in import attach_trade_in_valuation
from app.valuation.models.valuation import ValuationSource
from app.valuation.schemas.valuation import ValuationCreate
from app.valuation.services.valuation import create_valuation
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant
from app.vehicle.models.configuration import (
    ConfigurationMatchMethod,
    ConfigurationMode,
    ConfigurationSource,
    VehicleConfiguration,
)
from app.vehicle.models.spec_block import SPEC_BLOCK_ALL_FIELDS
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.public import CONFIGURATION_MODE_BUILD, CONFIGURATION_MODE_RECORD
from app.vehicle.schemas.configuration import ConfigurationCreate, ConfigurationOptionInput, ConfigurationUpdate
from app.vehicle.schemas.spec_block import VehicleSpecBlockInput
from app.vehicle.services import configuration as configuration_service
from app.vehicle.services import configuration_host

# --- fixtures -----------------------------------------------------------


def _session_factory(engine):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return lambda: factory()


def _dealership(db_session) -> Dealership:
    group = DealerGroup(name="Garage AG group")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        id=uuid.uuid4(), dealer_group_id=group.id, legal_name="Garage AG",
        dealer_license_number=f"ZH-{uuid.uuid4().hex[:6]}", license_state="ZH",
        franchise_type=FranchiseType.INDEPENDENT, address_street="Bahnhofstrasse", address_house_number="1",
        address_postal_code="8001", address_locality="Zürich", address_canton="ZH", phone="+41441234567",
        tax_id="CHE-123.456.789", vat_rate=Decimal("8.10"),
    )
    db_session.add(dealership)
    db_session.commit()
    return dealership


def _variant(db_session, **spec) -> ModelVariant:
    brand = Brand(code=f"b-{uuid.uuid4().hex[:6]}", display_name="Volkswagen")
    db_session.add(brand)
    db_session.flush()
    group = ModelGroup(brand_id=brand.id, name="ID.4")
    db_session.add(group)
    db_session.flush()
    variant = ModelVariant(
        model_group_id=group.id, name="Pro Performance", model_year_from=2024, vehicle_kind="passenger_car",
        ps=204, kw=150, base_price=Decimal("52900.00"), base_price_year=2025, **spec,
    )
    db_session.add(variant)
    db_session.commit()
    return variant


def _build_configuration(db_session, tenant_id, variant=None) -> VehicleConfiguration:
    variant = variant or _variant(db_session)
    config = configuration_service.create_configuration(
        db_session, tenant_id=tenant_id, actor_id=uuid.uuid4(),
        data=ConfigurationCreate(
            source=ConfigurationSource.PROVIDER, mode=ConfigurationMode.BUILD,
            match_method=ConfigurationMatchMethod.CATALOGUE_BROWSE, catalogue_variant_id=variant.id,
            exterior_colour="Kingsred Metallic", exterior_colour_surcharge=Decimal("990.00"),
        ),
    )
    db_session.commit()
    return configuration_service.replace_options(
        db_session, configuration=config, actor_id=uuid.uuid4(),
        options=[
            ConfigurationOptionInput(description="Wärmepumpe", option_code="HP1", price=Decimal("1250.00")),
            ConfigurationOptionInput(description="Panoramadach", option_code="PD2", price=Decimal("1490.00")),
            ConfigurationOptionInput(
                description="Abgewählt", option_code="XX9", price=Decimal("500.00"), selected=False
            ),
            ConfigurationOptionInput(description="Serienmässig", option_code="SR0", price=Decimal(0), is_included=True),
        ],
    )


def _record_configuration(db_session, tenant_id, *, vin=None, plate="ZH123456") -> VehicleConfiguration:
    config = configuration_service.create_configuration(
        db_session, tenant_id=tenant_id, actor_id=uuid.uuid4(),
        data=ConfigurationCreate(
            source=ConfigurationSource.MANUAL, mode=ConfigurationMode.RECORD,
            match_method=ConfigurationMatchMethod.KONTROLLSCHILD,
            brand_display_name="Subaru", model_group_name="Justy", variant_name="G3X Limousine",
            licence_plate=plate, vin=vin, first_registration_date=dt.date(2003, 10, 3), mileage_km=148000,
            spec=VehicleSpecBlockInput(ps=92),
        ),
    )
    db_session.commit()
    return config


def _message(db_session, event_type, aggregate_id) -> OutboxMessage:
    return db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == event_type, OutboxMessage.aggregate_id == aggregate_id)
    ).one()


def _stock_consumes_the_confirmation(db_session, contract) -> None:
    consume_once(
        db_session, message=_message(db_session, "sales.contract.confirmed", contract.id),
        consumer_name="inventory.sales_contract_confirmed", handler=handle_sales_contract_confirmed_message,
    )


class _NoVehicleMdmWrites:
    """Flags any VehicleMdm reaching a flush — ADR-070's runtime guard,
    applied across every session the scenario opens."""

    def __init__(self) -> None:
        self.flagged: list[str] = []

    def __enter__(self):
        event.listen(Session, "before_flush", self._guard)
        return self

    def _guard(self, session, _ctx, _instances):
        for obj in list(session.new) + list(session.dirty):
            if isinstance(obj, VehicleMdm):
                self.flagged.append(repr(obj))

    def __exit__(self, *exc):
        event.remove(Session, "before_flush", self._guard)
        return False


def _bearer(tenant_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.SALES}), is_dealer_manager=False,
    )
    return {"Authorization": f"Bearer {token}"}


# --- FR-C-12: offer Path B ------------------------------------------------


def test_the_public_mode_constants_are_the_stored_enum_values():
    # They are literals so the ADR-047 guard can follow them; this keeps
    # them from drifting from the enum the column stores.
    assert CONFIGURATION_MODE_BUILD == ConfigurationMode.BUILD.value
    assert CONFIGURATION_MODE_RECORD == ConfigurationMode.RECORD.value
    assert {CONFIGURATION_MODE_BUILD, CONFIGURATION_MODE_RECORD} == {m.value for m in ConfigurationMode}


def test_path_b_attaches_a_build_configuration_and_prefills_pricing(db_session):
    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())

    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id),
        actor_id=uuid.uuid4(),
    )

    assert offer.vehicle_source == "manual"
    assert offer.stock_item_id is None
    assert offer.configuration_id == config.id
    assert offer.vehicle_label == "Volkswagen ID.4 Pro Performance"
    assert offer.manual_vehicle_condition == "new"
    assert offer.manual_base_price == Decimal("52900.00")
    lines = db_session.scalars(
        select(SalesLineItem)
        .where(SalesLineItem.offer_id == offer.id, SalesLineItem.kind == LineItemKind.FACTORY_OPTION)
        .order_by(SalesLineItem.position)
    ).all()
    # selected, priced, not standard; then the colour surcharge
    assert [(li.label, li.unit_price) for li in lines] == [
        ("Wärmepumpe", Decimal("1250.00")),
        ("Panoramadach", Decimal("1490.00")),
        ("Kingsred Metallic", Decimal("990.00")),
    ]
    assert offer.options_total == Decimal("3730.00")
    assert offer.list_price == Decimal("56630.00")


def test_path_b_freezes_the_spec_block_as_its_third_carrier(db_session):
    """ADR-071: the host snapshot carries the whole block."""

    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id),
        actor_id=uuid.uuid4(),
    )

    spec = offer.vehicle_snapshot["spec"]
    assert {to_camel(f) for f in SPEC_BLOCK_ALL_FIELDS} <= set(spec)
    assert spec["ps"] == 204
    assert offer.vehicle_snapshot["configurationId"] == str(config.id)


def test_path_b_refuses_a_record_configuration(db_session):
    """Mode matrix: no `record` mode is reachable from offer Path B."""

    dealership = _dealership(db_session)
    record = _record_configuration(db_session, dealership.id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())

    with pytest.raises(UnprocessableEntityError) as refused:
        update_offer(
            db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=record.id),
            actor_id=uuid.uuid4(),
        )
    assert refused.value.details["reason"] == "configuration_mode_not_allowed"


def test_path_b_refuses_another_tenants_configuration_with_a_404(db_session):
    dealership = _dealership(db_session)
    foreign = _build_configuration(db_session, uuid.uuid4())
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())

    with pytest.raises(NotFoundError):
        update_offer(
            db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=foreign.id),
            actor_id=uuid.uuid4(),
        )


def test_choosing_stock_after_a_configuration_detaches_it(db_session):
    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    item = create_stock_item(
        db_session, tenant_id=dealership.id, actor_id=None,
        data=StockItemCreate(vehicle_label="Skoda Enyaq", condition=StockItemCondition.NEW, vin="TMBJC9NY0RF000001"),
    )
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    update_offer(db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id), actor_id=None)

    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
        actor_id=None,
    )

    assert offer.configuration_id is None
    assert offer.vehicle_snapshot["_identity"] == f"stock:{item.id}"


def test_editing_the_configuration_refreezes_a_draft_and_never_an_open_offer(db_session):
    """FR-C-16's failure case: a configuration changing under a live offer
    must not silently change what the customer was quoted."""

    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id), actor_id=None
    )
    frozen_ps = offer.vehicle_snapshot["spec"]["ps"]

    configuration_service.update_configuration(
        db_session, configuration=config, actor_id=uuid.uuid4(), data=ConfigurationUpdate(spec=VehicleSpecBlockInput(ps=286)),
    )
    # A draft offer that touches the container again picks the new version up …
    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id), actor_id=None
    )
    assert offer.vehicle_snapshot["spec"]["ps"] == 286 != frozen_ps
    # … an open (generated) offer can no longer be edited at all.
    offer.status = offer.status.__class__.OPEN
    db_session.commit()
    with pytest.raises(ConflictError):
        update_offer(
            db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id), actor_id=None
        )


def test_path_b_api_round_trip(client, db_session):
    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    headers = _bearer(dealership.id)
    created = client.post("/v1/sales/offers", json={}, headers={**headers, "Idempotency-Key": str(uuid.uuid4())})
    assert created.status_code == 201, created.text
    offer_id = created.json()["id"]

    patched = client.patch(
        f"/v1/sales/offers/{offer_id}", json={"configurationId": str(config.id)},
        headers={**headers, "If-Match": str(created.json()["version"])},
    )

    assert patched.status_code == 200, patched.text
    assert patched.json()["configurationId"] == str(config.id)
    assert patched.json()["configurationLabel"] == "Volkswagen ID.4 Pro Performance"


# --- contract confirmation → pipeline item, no vehicle-mdm ----------------


def test_confirming_a_configured_contract_creates_the_pipeline_item_with_its_configuration(db_session, engine):
    """Exit criterion 6."""

    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    group_id = uuid.uuid4()

    with _NoVehicleMdmWrites() as guard:
        offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
        offer = update_offer(
            db_session, offer=offer, group_id=group_id, data=OfferUpdate(configuration_id=config.id), actor_id=None
        )
        contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
        assert contract.configuration_id == config.id
        contract = confirm_contract(
            db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(),
            session_factory=_session_factory(engine),
        )
        payload = _message(db_session, "sales.contract.confirmed", contract.id).payload
        assert payload["manualConfiguration"]["configurationId"] == str(config.id)
        _stock_consumes_the_confirmation(db_session, contract)

    item = db_session.scalars(select(StockItem).where(StockItem.pipeline_ref == f"contract:{contract.id}:manual")).one()
    assert item.configuration_id == config.id
    assert item.configuration_label == "Volkswagen ID.4 Pro Performance"
    assert item.lifecycle_status == LifecycleStatus.PIPELINE
    assert item.vehicle_id is None
    assert guard.flagged == []
    assert db_session.query(VehicleMdm).count() == 0


# --- FR-C-13: add to pipeline from the Stock list ------------------------


@pytest.mark.parametrize("mode", ["build", "record"])
def test_the_pipeline_accepts_both_modes(db_session, mode):
    tenant_id = uuid.uuid4()
    config = (
        _build_configuration(db_session, tenant_id) if mode == "build" else _record_configuration(db_session, tenant_id)
    )

    item = create_stock_item(
        db_session, tenant_id=tenant_id, actor_id=None,
        data=StockItemCreate(
            vehicle_label="from the configurator",
            condition=StockItemCondition.NEW if mode == "build" else StockItemCondition.USED,
            configuration_id=config.id,
        ),
    )

    assert item.configuration_id == config.id
    assert item.configuration_label == configuration_host.configuration_label(config)
    assert item.lifecycle_status == LifecycleStatus.PIPELINE


def test_the_pipeline_refuses_another_tenants_configuration(db_session):
    foreign = _record_configuration(db_session, uuid.uuid4())
    with pytest.raises(NotFoundError):
        create_stock_item(
            db_session, tenant_id=uuid.uuid4(), actor_id=None,
            data=StockItemCreate(vehicle_label="x", condition=StockItemCondition.USED, configuration_id=foreign.id),
        )


def test_a_used_car_bought_in_reaches_a_contract_without_ever_being_configured_in_the_offer(db_session, engine):
    """Exit criterion 3, end to end: record via FR-C-13 → pipeline stock item
    → Path A finds it → it reaches a confirmed contract. The offer never
    holds a configuration of its own."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    bought_in = _record_configuration(db_session, dealership.id)
    item = create_stock_item(
        db_session, tenant_id=dealership.id, actor_id=None,
        data=StockItemCreate(
            vehicle_label=configuration_host.configuration_label(bought_in), condition=StockItemCondition.USED,
            list_price=Decimal("6900.00"), configuration_id=bought_in.id,
        ),
    )

    # Path A: the offer workspace's stock search
    from app.core.pagination import SortPageParams
    from app.inventory.api.stock_items import _DEFAULT_STOCK_ITEM_SORT

    found, *_ = list_stock_items(
        db_session, tenant_id=dealership.id, q="Justy", lifecycle_status=None,
        params=SortPageParams(limit=20, cursor=None, sort_fields=_DEFAULT_STOCK_ITEM_SORT),
    )
    assert [i.id for i in found] == [item.id]

    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=group_id,
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
        actor_id=None,
    )
    assert offer.configuration_id is None
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    contract = confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine)
    )

    assert contract.status.value == "confirmed"
    assert contract.stock_item_id == item.id
    assert contract.configuration_id is None


# --- FR-C-14: valuation ---------------------------------------------------


def test_a_valuation_from_a_record_configuration_writes_no_vehicle(db_session):
    dealership = _dealership(db_session)
    config = _record_configuration(db_session, dealership.id, vin="JF1GD2DA03G000001")

    with _NoVehicleMdmWrites() as guard:
        valuation = create_valuation(
            db_session, tenant_id=dealership.id, group_id=uuid.uuid4(), actor_id=uuid.uuid4(),
            data=ValuationCreate(
                configuration_id=config.id, source=ValuationSource.MANUAL, final_offer=Decimal("2500.00"),
            ),
        )

    assert guard.flagged == []
    assert db_session.query(VehicleMdm).count() == 0
    assert valuation.configuration_id == config.id
    assert valuation.vehicle_id is None
    assert (valuation.vehicle_make, valuation.vehicle_model, valuation.vehicle_trim) == ("Subaru", "Justy", "G3X Limousine")
    assert valuation.vehicle_plate == "ZH123456"
    assert valuation.vehicle_vin == "JF1GD2DA03G000001"
    assert valuation.mileage == 148000


def test_a_valuation_refuses_a_build_configuration(db_session):
    """Mode matrix: the valuation host is `record` only."""

    dealership = _dealership(db_session)
    build = _build_configuration(db_session, dealership.id)
    with pytest.raises(UnprocessableEntityError) as refused:
        create_valuation(
            db_session, tenant_id=dealership.id, group_id=uuid.uuid4(), actor_id=None,
            data=ValuationCreate(configuration_id=build.id, source=ValuationSource.MANUAL, final_offer=Decimal(1)),
        )
    assert refused.value.details["reason"] == "configuration_mode_not_allowed"


def test_a_valuation_without_a_configuration_behaves_as_before(db_session):
    """FR-V-17's other half stands: no configuration, a VIN still resolves
    or creates the vehicle in the same step."""

    dealership = _dealership(db_session)
    valuation = create_valuation(
        db_session, tenant_id=dealership.id, group_id=uuid.uuid4(), actor_id=None,
        data=ValuationCreate(vin="WVWZZZ1KZAW000777", source=ValuationSource.MANUAL, final_offer=Decimal(1)),
    )
    assert valuation.vehicle_id is not None
    assert valuation.configuration_id is None


# --- the trade-in carve-out ----------------------------------------------


def test_a_trade_in_identified_from_its_plate_reaches_the_offer_as_a_valuation_reference(db_session, engine):
    """Exit criterion 4: plate → record configuration → valuation → the
    offer references the valuation; confirmation makes the trade-in a
    pipeline item carrying both. No vehicle-mdm record at any point."""

    from tests.test_vehicle_identification import _connected_tenant

    tenant_id = _connected_tenant(db_session)
    group = DealerGroup(name="g")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        id=tenant_id, dealer_group_id=group.id, legal_name="Garage AG", dealer_license_number="ZH-9",
        license_state="ZH", franchise_type=FranchiseType.INDEPENDENT, address_street="B", address_house_number="1",
        address_postal_code="8001", address_locality="Zürich", address_canton="ZH", phone="+41441234567",
        tax_id="CHE-123.456.789", vat_rate=Decimal("8.10"),
    )
    db_session.add(dealership)
    db_session.commit()
    group_id = uuid.uuid4()

    from app.vehicle.services import identification

    with _NoVehicleMdmWrites() as guard:
        found = identification.identify(db_session, tenant_id=tenant_id, actor_id=None, query="BE 123 456")
        (candidate,) = found.variants
        trade_in_config = configuration_service.create_configuration(
            db_session, tenant_id=tenant_id, actor_id=uuid.uuid4(),
            data=ConfigurationCreate(
                source=ConfigurationSource.PROVIDER, mode=ConfigurationMode.RECORD,
                match_method=found.match_method, catalogue_variant_id=candidate.catalogue_variant_id,
                licence_plate=found.observed.licence_plate, stammnummer=found.observed.stammnummer,
                type_approval_number=found.observed.type_approval_number,
                first_registration_date=found.observed.first_registration_date, mileage_km=61000,
            ),
        )
        valuation = create_valuation(
            db_session, tenant_id=tenant_id, group_id=group_id, actor_id=None,
            data=ValuationCreate(
                configuration_id=trade_in_config.id, source=ValuationSource.MANUAL, final_offer=Decimal("31500.00"),
            ),
        )

        sold = _build_configuration(db_session, tenant_id)
        offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
        offer = update_offer(db_session, offer=offer, group_id=group_id, data=OfferUpdate(configuration_id=sold.id), actor_id=None)
        offer = attach_trade_in_valuation(db_session, offer=offer, valuation_id=valuation.id, actor_id=None)

        assert offer.trade_in_valuation_id == valuation.id
        assert offer.trade_in_configuration_id == trade_in_config.id
        # brand · model group · variant, as the mock catalogue names them
        assert offer.trade_in_label == "Volkswagen Golf Golf GTI 2.0 TSI DSG"
        assert offer.trade_in_vehicle_id is None
        assert offer.trade_in_value == Decimal("31500.00")

        contract = create_contract(db_session, tenant_id=tenant_id, offer=offer, actor_id=uuid.uuid4())
        contract = confirm_contract(
            db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(),
            session_factory=_session_factory(engine),
        )
        _stock_consumes_the_confirmation(db_session, contract)

    trade_in_item = db_session.scalars(
        select(StockItem).where(StockItem.pipeline_ref == f"contract:{contract.id}:trade_in")
    ).one()
    assert trade_in_item.configuration_id == trade_in_config.id
    assert trade_in_item.valuation_ref_id == valuation.id
    assert guard.flagged == []
    assert db_session.query(VehicleMdm).count() == 0


# --- FR-C-15: Vehicle 360, Customer 360 through the vehicle ---------------


def test_vehicle_360_reads_this_tenants_configuration_of_the_vehicle(client, db_session):
    tenant_id = uuid.uuid4()
    vehicle = VehicleMdm(vin="JF1GD2DA03G000002", vehicle_number="F-000777")
    db_session.add(vehicle)
    db_session.commit()
    config = _record_configuration(db_session, tenant_id, vin="JF1GD2DA03G000002")
    assert config.vehicle_id == vehicle.id

    mine = client.get(f"/v1/vehicle-mdm/{vehicle.id}/configuration", headers=_bearer(tenant_id))
    theirs = client.get(f"/v1/vehicle-mdm/{vehicle.id}/configuration", headers=_bearer(uuid.uuid4()))

    assert mine.status_code == 200, mine.text
    assert mine.json()["id"] == str(config.id)
    assert theirs.status_code == 404


def test_no_route_reaches_a_configuration_through_a_customer():
    """A configuration has no customer; Customer 360 reaches it only through
    the vehicle. A customer-keyed configuration route would be the second,
    competing ownership link beside VehicleParty."""

    from app.main import app

    for route in app.routes:
        path = getattr(route, "path", "")
        if "configuration" in path:
            assert "customer" not in path, path


# --- FR-C-16: re-sync, never automatic, per field ------------------------


def test_resync_lists_the_disagreeing_fields_and_marks_overrides(db_session):
    tenant_id = uuid.uuid4()
    variant = _variant(db_session, displacement_ccm=None)
    config = _build_configuration(db_session, tenant_id, variant=variant)
    config = configuration_service.update_configuration(
        db_session, configuration=config, actor_id=uuid.uuid4(), data=ConfigurationUpdate(spec=VehicleSpecBlockInput(ps=210)),
    )
    # the catalogue moves on; the configuration must not follow by itself
    variant.kw = 160
    variant.displacement_ccm = 0
    db_session.commit()
    db_session.refresh(config)
    assert config.kw == 150

    fields = {f.field: f for f in configuration_host.preview_resync(db_session, configuration=config)}

    assert set(fields) == {"ps", "kw", "displacement_ccm"}
    assert fields["ps"].overridden is True and fields["ps"].current == 210 and fields["ps"].catalogue == 204
    assert fields["kw"].overridden is False


def test_resync_applies_only_the_chosen_fields(db_session):
    tenant_id = uuid.uuid4()
    variant = _variant(db_session)
    config = _build_configuration(db_session, tenant_id, variant=variant)
    config = configuration_service.update_configuration(
        db_session, configuration=config, actor_id=uuid.uuid4(), data=ConfigurationUpdate(spec=VehicleSpecBlockInput(ps=210)),
    )
    variant.kw = 160
    db_session.commit()

    config = configuration_host.apply_resync(db_session, configuration=config, actor_id=uuid.uuid4(), fields=["kw"])

    assert config.kw == 160
    assert config.ps == 210  # overridden and not chosen: kept
    assert "ps" in config.overridden_fields


def test_resync_of_an_overridden_field_clears_the_override(db_session):
    tenant_id = uuid.uuid4()
    config = _build_configuration(db_session, tenant_id)
    config = configuration_service.update_configuration(
        db_session, configuration=config, actor_id=uuid.uuid4(), data=ConfigurationUpdate(spec=VehicleSpecBlockInput(ps=210)),
    )

    config = configuration_host.apply_resync(db_session, configuration=config, actor_id=uuid.uuid4(), fields=["ps"])

    assert config.ps == 204
    assert "ps" not in config.overridden_fields


def test_resync_needs_a_catalogue_variant(db_session):
    config = _record_configuration(db_session, uuid.uuid4())
    with pytest.raises(ConflictError):
        configuration_host.preview_resync(db_session, configuration=config)


def test_resync_api_needs_if_match_and_a_field(client, db_session):
    tenant_id = uuid.uuid4()
    config = _build_configuration(db_session, tenant_id)
    headers = _bearer(tenant_id)

    preview = client.get(f"/v1/configurations/{config.id}/resync", headers=headers)
    assert preview.status_code == 200, preview.text
    assert preview.json() == []
    assert client.patch(f"/v1/configurations/{config.id}/resync", json={"fields": ["ps"]}, headers=headers).status_code == 400
    empty = client.patch(
        f"/v1/configurations/{config.id}/resync", json={"fields": []}, headers={**headers, "If-Match": str(config.version)}
    )
    assert empty.status_code == 422


def test_nothing_resyncs_a_configuration_automatically():
    """Never automatic: apply_resync has exactly one caller, the endpoint."""

    from pathlib import Path

    callers = [
        p for p in Path("app").rglob("*.py")
        if "apply_resync(" in p.read_text() and p.name != "configuration_host.py"
    ]
    assert [str(p) for p in callers] == ["app/vehicle/api/configuration.py"]


# --- the offer document ---------------------------------------------------


@pytest.mark.parametrize(
    ("language", "vat_label"),
    [(SwissLanguage.FR, "TVA"), (SwissLanguage.IT, "IVA"), (SwissLanguage.DE, "MWST"), (SwissLanguage.EN, "VAT")],
)
def test_the_offer_document_itemises_the_configuration_with_one_vat_line(db_session, language, vat_label):
    """Exit criterion 9, re-verified on this code: the configuration's price
    lines, in the customer's language, with exactly one VAT line."""

    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id), actor_id=None
    )

    content = build_offer_content(
        offer, language=language, vat_rate=dealership.vat_rate, option_lines=_included_option_lines(db_session, offer=offer)
    )
    (block,) = [b for b in content.blocks if isinstance(b, LineItemsBlock)]
    labels = [line.label for line in block.lines]

    assert labels[1:4] == ["Wärmepumpe", "Panoramadach", "Kingsred Metallic"]  # provider text, as delivered
    vat_lines = [label for label in labels if vat_label.lower() in label.lower()]
    assert len(vat_lines) == 1, labels


def test_sales_reconciliation_checks_every_configuration_reference():
    labels = {getattr(c, "label", "") for c in SALES_CHECKS}
    assert {
        "sales_offer.configuration_id -> vehicle_configuration.id",
        "sales_offer.trade_in_configuration_id -> vehicle_configuration.id",
        "sales_contract.configuration_id -> vehicle_configuration.id",
        "sales_contract.trade_in_configuration_id -> vehicle_configuration.id",
    } <= labels


# --- review findings (KAN-10) ---------------------------------------------


def test_copying_a_configured_offer_keeps_the_configuration_and_its_lines(db_session, engine):
    from app.sales.services.offer import copy_offer

    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    trade_in_config = _record_configuration(db_session, dealership.id)
    valuation = create_valuation(
        db_session, tenant_id=dealership.id, group_id=uuid.uuid4(), actor_id=None,
        data=ValuationCreate(configuration_id=trade_in_config.id, source=ValuationSource.MANUAL, final_offer=Decimal("2500.00")),
    )
    source = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    source = update_offer(db_session, offer=source, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id), actor_id=None)
    source = attach_trade_in_valuation(db_session, offer=source, valuation_id=valuation.id, actor_id=None)

    copy = copy_offer(db_session, source=source, actor_id=uuid.uuid4())

    assert copy.configuration_id == config.id
    assert copy.trade_in_configuration_id == trade_in_config.id
    assert copy.options_total == source.options_total == Decimal("3730.00")
    assert copy.list_price == source.list_price

    group_id = uuid.uuid4()
    contract = create_contract(db_session, tenant_id=dealership.id, offer=copy, actor_id=uuid.uuid4())
    contract = confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine)
    )
    payload = _message(db_session, "sales.contract.confirmed", contract.id).payload
    assert payload["manualConfiguration"]["configurationId"] == str(config.id)
    assert payload["tradeIn"]["configurationId"] == str(trade_in_config.id)


def test_reattaching_the_same_configuration_keeps_the_sellers_base_price_and_line_edits(db_session):
    from app.sales.schemas.line_item import LineItemFactoryOptionPatch, LineItemsReplaceRequest
    from app.sales.services.line_items import replace_line_items

    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = update_offer(db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id), actor_id=None)
    offer = update_offer(db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(manual_base_price=Decimal("49000.00")), actor_id=None)
    lines = db_session.scalars(
        select(SalesLineItem).where(SalesLineItem.offer_id == offer.id).order_by(SalesLineItem.position)
    ).all()
    replace_line_items(
        db_session, offer=offer, actor_id=None,
        data=LineItemsReplaceRequest(
            factory_options=[
                LineItemFactoryOptionPatch(
                    id=li.id,
                    included=li.code != "PD2",
                    discount_type="amount" if li.code == "HP1" else None,
                    discount_value=Decimal("250.00") if li.code == "HP1" else None,
                )
                for li in lines
            ]
        ),
    )

    # the advisor reopens the configuration, changes something, saves
    configuration_service.update_configuration(
        db_session, configuration=config, actor_id=uuid.uuid4(), data=ConfigurationUpdate(notes="Kunde wünscht Lieferung im März"),
    )
    offer = update_offer(db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id), actor_id=None)

    assert offer.manual_base_price == Decimal("49000.00")
    by_code = {
        li.code: li for li in db_session.scalars(select(SalesLineItem).where(SalesLineItem.offer_id == offer.id)).all()
    }
    assert by_code["PD2"].included is False
    assert by_code["HP1"].discount_resolved_amount == Decimal("250.00")
    assert offer.options_total == Decimal("1250.00") - Decimal("250.00") + Decimal("990.00")


def test_a_stock_offer_document_is_not_itemised(db_session):
    """Path A is out of scope: only a configured offer itemises its lines."""

    dealership = _dealership(db_session)
    item = create_stock_item(
        db_session, tenant_id=dealership.id, actor_id=None,
        data=StockItemCreate(vehicle_label="Skoda Enyaq", condition=StockItemCondition.NEW, vin="TMBJC9NY0RF000002"),
    )
    from app.inventory.schemas.pricing import OptionInput
    from app.inventory.services.pricing import set_options

    set_options(
        db_session, item=item, base_price=Decimal("48000.00"), actor_id=None,
        options=[OptionInput(code="WP", label="Wärmepumpe", price=Decimal("1200.00"))],
    )
    db_session.commit()
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label), actor_id=None,
    )
    # the stock offer does carry a factory-option line …
    assert db_session.scalar(
        select(SalesLineItem).where(SalesLineItem.offer_id == offer.id, SalesLineItem.kind == LineItemKind.FACTORY_OPTION)
    ) is not None
    # … and its document still shows only the options total, as before
    assert _included_option_lines(db_session, offer=offer) == []


def test_a_saved_configurations_mode_cannot_change_under_its_host(db_session):
    """The mode matrix holds after attach too: a Path B configuration cannot
    be turned into a record one behind the offer's back."""

    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    update_offer(db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id), actor_id=None)

    with pytest.raises(UnprocessableEntityError) as refused:
        configuration_service.update_configuration(
            db_session, configuration=config, actor_id=uuid.uuid4(), data=ConfigurationUpdate(mode=ConfigurationMode.RECORD),
        )
    assert refused.value.details["reason"] == "configuration_mode_fixed"
    # resending the same mode (what the overlay does on every save) is fine
    configuration_service.update_configuration(
        db_session, configuration=config, actor_id=uuid.uuid4(), data=ConfigurationUpdate(mode=ConfigurationMode.BUILD),
    )
