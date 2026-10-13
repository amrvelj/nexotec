"""WP-8 PR-5: one-step trade-in (S-D18/ADR-064)."""

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.errors import ConflictError, NotFoundError
from app.customer.public import VehiclePartyRole, list_vehicle_parties
from app.sales.models.offer import SalesOffer
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.offer import cancel_offer, create_offer, update_offer
from app.sales.services.trade_in import attach_trade_in_valuation, set_trade_in
from app.valuation.models.valuation import ValuationSource
from app.valuation.schemas.valuation import ValuationCreate
from app.valuation.services.valuation import create_valuation
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services import vehicle_mdm as vehicle_mdm_service
from tests.test_inventory_vin_resolution import _lose_the_first_lookup

_postgres_only = pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"),
    reason="the insert race needs a second connection that sees the first one's commit — Postgres lane",
)


def _offer_with_customer(db_session, tenant_id, customer_id):
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    offer.customer_id = customer_id
    db_session.commit()
    return offer


def test_set_trade_in_records_vehicle_and_allocates_owner_and_keeper(db_session):
    from app.customer.models.customer import Customer, CustomerType, Language

    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    customer = Customer(
        group_id=group_id, customer_number="K-300001", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name="Trade", last_name="In",
    )
    db_session.add(customer)
    db_session.flush()
    customer_id = customer.id
    offer = _offer_with_customer(db_session, tenant_id, customer_id)

    updated = set_trade_in(
        db_session,
        deal=offer,
        group_id=group_id,
        vin="WVWZZZ1KZAW123456",
        plate=None,
        canton=None,
        vehicle_label="Skoda Octavia Combi 1.5 TSI",
        customer_id=None,
        actor_id=uuid.uuid4(),
    )

    assert updated.trade_in_vehicle_id is not None
    assert updated.trade_in_vin == "WVWZZZ1KZAW123456"
    assert updated.trade_in_label == "Skoda Octavia Combi 1.5 TSI"

    parties = list_vehicle_parties(db_session, vehicle_id=updated.trade_in_vehicle_id, group_id=group_id)
    roles = {p.role for p in parties if p.customer_id == customer_id}
    assert roles == {VehiclePartyRole.OWNER, VehiclePartyRole.KEEPER}


def test_set_trade_in_auto_attaches_an_existing_valid_valuation(db_session):
    """FR-S-08: "an existing valid valuation is offered before a new one
    is made.\""""

    tenant_id = uuid.uuid4()
    vin = "WVWZZZ1KZAW654321"
    create_valuation(
        db_session, tenant_id=tenant_id, group_id=uuid.uuid4(),
        data=ValuationCreate(vin=vin, source=ValuationSource.AUTO_I_DAT, final_offer=Decimal(15350)),
        actor_id=uuid.uuid4(),
    )

    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    updated = set_trade_in(
        db_session, deal=offer, group_id=uuid.uuid4(), vin=vin, plate=None, canton=None,
        vehicle_label="Skoda Octavia", customer_id=None, actor_id=uuid.uuid4(),
    )

    assert updated.trade_in_valuation_id is not None
    assert updated.trade_in_value == Decimal(15350)
    assert updated.trade_in_purchase_price == Decimal(15350)


def test_trade_in_purchase_price_is_seller_adjustable_independently(db_session):
    tenant_id = uuid.uuid4()
    vin = "WVWZZZ1KZAW111111"
    create_valuation(
        db_session, tenant_id=tenant_id, group_id=uuid.uuid4(),
        data=ValuationCreate(vin=vin, source=ValuationSource.MANUAL, final_offer=Decimal(12000)),
        actor_id=uuid.uuid4(),
    )
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    offer = set_trade_in(
        db_session, deal=offer, group_id=uuid.uuid4(), vin=vin, plate=None, canton=None,
        vehicle_label="Fiat 500", customer_id=None, actor_id=uuid.uuid4(),
    )

    offer.trade_in_purchase_price = Decimal(11500)  # a seller negotiation, distinct from the valuation's own figure
    db_session.commit()

    assert offer.trade_in_value == Decimal(12000)  # the customer-facing figure is unchanged
    assert offer.trade_in_purchase_price == Decimal(11500)


def test_attach_trade_in_valuation_explicit(db_session):
    tenant_id = uuid.uuid4()
    vin = "WVWZZZ1KZAW222222"
    create_valuation(
        db_session, tenant_id=tenant_id, group_id=uuid.uuid4(),
        data=ValuationCreate(vin=vin, source=ValuationSource.MANUAL, final_offer=Decimal(9000)),
        actor_id=uuid.uuid4(),
    )
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    offer = set_trade_in(
        db_session, deal=offer, group_id=uuid.uuid4(), vin=vin, plate=None, canton=None,
        vehicle_label="Seat Leon", customer_id=None, actor_id=uuid.uuid4(),
    )
    # A second, explicitly attached valuation overrides the auto-attached one.
    second = create_valuation(
        db_session, tenant_id=tenant_id, group_id=uuid.uuid4(),
        data=ValuationCreate(vin=vin, source=ValuationSource.MANUAL, final_offer=Decimal(9500)),
        actor_id=uuid.uuid4(),
    )
    offer = attach_trade_in_valuation(db_session, deal=offer, valuation_id=second.id, actor_id=uuid.uuid4())
    assert offer.trade_in_valuation_id == second.id
    assert offer.trade_in_value == Decimal(9500)


def test_attaching_a_valuation_to_an_offer_that_left_draft_is_refused(db_session):
    """KAN-67: attach_trade_in_valuation now checks, like set_trade_in always
    did, that the deal is still editable — a non-draft offer is not."""

    tenant_id = uuid.uuid4()
    valuation = create_valuation(
        db_session, tenant_id=tenant_id, group_id=uuid.uuid4(),
        data=ValuationCreate(vin="WVWZZZ1KZAW333333", source=ValuationSource.MANUAL, final_offer=Decimal(9000)),
        actor_id=uuid.uuid4(),
    )
    offer = cancel_offer(
        db_session, offer=create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4()),
        reason="Kunde verzichtet", actor_id=uuid.uuid4(),
    )

    with pytest.raises(ConflictError):
        attach_trade_in_valuation(db_session, deal=offer, valuation_id=valuation.id, actor_id=uuid.uuid4())

    db_session.refresh(offer)
    assert offer.trade_in_valuation_id is None


def test_plate_only_without_a_decisive_match_is_refused(db_session):
    tenant_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())

    with pytest.raises(ConflictError):
        set_trade_in(
            db_session, deal=offer, group_id=uuid.uuid4(), vin=None, plate="ZH123456", canton="ZH",
            vehicle_label="Unknown car", customer_id=None, actor_id=uuid.uuid4(),
        )


def test_payable_is_gross_price_minus_trade_in_value(db_session):
    from app.inventory.models.stock_item import StockItemCondition
    from app.inventory.schemas.stock_item import StockItemCreate
    from app.inventory.services.stock_item import create_stock_item
    from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType

    group = DealerGroup(name="Garage AG group")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        id=uuid.uuid4(), dealer_group_id=group.id, legal_name="Garage AG", dealer_license_number="ZH-1",
        license_state="ZH", franchise_type=FranchiseType.INDEPENDENT, address_street="Bahnhofstrasse",
        address_house_number="1", address_postal_code="8001", address_locality="Zürich", address_canton="ZH",
        phone="+41441234567", tax_id="CHE-123.456.789",
    )
    db_session.add(dealership)
    db_session.commit()
    tenant_id = dealership.id

    item = create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Mercedes-Benz GLC", condition=StockItemCondition.USED, vin="WBA4Y9F55LCE00001"),
        actor_id=uuid.uuid4(),
    )
    item.base_price = Decimal("82260.00")
    item.list_price = Decimal("82260.00")
    db_session.commit()

    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
        actor_id=uuid.uuid4(),
    )
    assert offer.gross_price == Decimal("82260.00")

    create_valuation(
        db_session, tenant_id=tenant_id, group_id=uuid.uuid4(),
        data=ValuationCreate(vin="WVWZZZ1KZAW333333", source=ValuationSource.MANUAL, final_offer=Decimal(17950)),
        actor_id=uuid.uuid4(),
    )
    offer = set_trade_in(
        db_session, deal=offer, group_id=uuid.uuid4(), vin="WVWZZZ1KZAW333333", plate=None, canton=None,
        vehicle_label="Skoda Octavia Combi 1.5 TSI", customer_id=None, actor_id=uuid.uuid4(),
    )

    assert offer.payable == Decimal("64310.00")


def test_trade_in_for_another_groups_customer_is_a_404(db_session):
    """KAN-99: body.customerId is resolved in the caller's group by
    allocate_vehicle_party — 404, never a cross-group VehicleParty."""

    from app.customer.models.customer import Customer, CustomerType, Language

    other_group = uuid.uuid4()
    stranger = Customer(
        group_id=other_group, customer_number="K-300099", customer_type=CustomerType.INDIVIDUAL,
        language=Language.DE, first_name="Fremd", last_name="Kunde",
    )
    db_session.add(stranger)
    db_session.commit()
    offer = create_offer(db_session, tenant_id=uuid.uuid4(), actor_id=uuid.uuid4())

    with pytest.raises(NotFoundError):
        set_trade_in(
            db_session, deal=offer, group_id=uuid.uuid4(), vin="WVWZZZ1KZAW444444", plate=None, canton=None,
            vehicle_label="VW Golf", customer_id=stranger.id, actor_id=uuid.uuid4(),
        )
    db_session.rollback()
    assert db_session.scalar(select(VehicleMdm).where(VehicleMdm.vin == "WVWZZZ1KZAW444444")) is None


# --- KAN-328: the vehicle a trade-in creates is the user's write, on a session of its own ----


def test_the_vehicle_a_trade_in_creates_names_the_user_who_recorded_it(db_session):
    """KAN-243: a null created_by means a system write; a user's trade-in is not one."""

    tenant_id, actor_id = uuid.uuid4(), uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=actor_id)

    updated = set_trade_in(
        db_session, deal=offer, group_id=uuid.uuid4(), vin="WVWZZZ1KZAW328001", plate=None, canton=None,
        vehicle_label="Skoda Octavia", customer_id=None, actor_id=actor_id,
    )

    vehicle = db_session.get(VehicleMdm, updated.trade_in_vehicle_id)
    assert vehicle is not None
    assert (vehicle.created_by, vehicle.updated_by) == (actor_id, actor_id)


@_postgres_only
def test_a_trade_in_that_loses_the_insert_race_records_the_winners_vehicle(db_session, engine, monkeypatch):
    """Two writers resolving a VIN vehicle-mdm has never seen both insert;
    the loser resolves to the winner's record (FR-V-15: same VIN, same car)
    instead of answering 409 — as Stock does since KAN-257."""

    vin = "WVWZZZ1KZAW328002"
    offer = create_offer(db_session, tenant_id=uuid.uuid4(), actor_id=uuid.uuid4())
    winners: list[uuid.UUID] = []

    def another_dealership_takes_the_car(other, raced_vin):
        winners.append(vehicle_mdm_service.create_vehicle_mdm(other, vin=raced_vin, catalogue_variant_id=None).id)

    misses = _lose_the_first_lookup(monkeypatch, engine, winner=another_dealership_takes_the_car)

    updated = set_trade_in(
        db_session, deal=offer, group_id=uuid.uuid4(), vin=vin, plate=None, canton=None,
        vehicle_label="Skoda Octavia", customer_id=None, actor_id=uuid.uuid4(),
    )

    assert misses == [vin]  # the race really happened
    assert updated.trade_in_vehicle_id == winners[0]
    assert updated.trade_in_vin == vin
    assert db_session.scalar(select(func.count()).select_from(VehicleMdm)) == 1


@_postgres_only
def test_losing_the_race_never_discards_the_trade_ins_own_pending_work(db_session, engine, monkeypatch):
    """vehicle-mdm's guard rolls back the session it is handed. The trade-in
    hands it a session of its own, so what the request had pending survives
    the lost race and lands with the trade-in's own commit."""

    vin = "WVWZZZ1KZAW328003"
    offer = create_offer(db_session, tenant_id=uuid.uuid4(), actor_id=uuid.uuid4())
    offer.vehicle_label = "The request's own pending change"
    db_session.flush()

    def another_dealership_takes_the_car(other, raced_vin):
        vehicle_mdm_service.create_vehicle_mdm(other, vin=raced_vin, catalogue_variant_id=None)

    _lose_the_first_lookup(monkeypatch, engine, winner=another_dealership_takes_the_car)

    set_trade_in(
        db_session, deal=offer, group_id=uuid.uuid4(), vin=vin, plate=None, canton=None,
        vehicle_label="Skoda Octavia", customer_id=None, actor_id=uuid.uuid4(),
    )

    assert db_session.scalar(
        select(SalesOffer.vehicle_label).where(SalesOffer.id == offer.id)
    ) == "The request's own pending change"
