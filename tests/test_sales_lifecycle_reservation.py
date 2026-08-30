"""WP-8 PR-6: contract confirmation, reservation (ADR-047 Pattern B), the
two distinct events (ADR-046), the ADR-052 is_invoiceable local replica,
and the ADR-065/S-D19 credit-block/do-not-contact guards.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.customer.schemas.customer import CustomerCreate, CustomerEmailCreate, CustomerUpdate
from app.customer.services.customer import create_customer, set_credit_block, update_customer
from app.inventory.models.stock_item import ReservationState, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.stock_item import create_stock_item, get_stock_item_or_404
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.sales.consumers import handle_stock_item_purchased
from app.sales.models.contract import ContractStatus
from app.sales.models.offer import OfferStatus
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import cancel_contract, confirm_contract, create_contract, request_invoice
from app.sales.services.offer import create_offer, update_offer


def _session_factory(engine):
    """A genuinely SEPARATE session bound to the same test engine —
    StaticPool means it shares the one underlying SQLite connection with
    db_session, so it sees the same data, but it has its own identity map:
    closing it (as confirm_contract's own short-lived session does) never
    expunges objects the test's own db_session is still holding, unlike
    passing db_session itself as the "second" session would.
    """

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return lambda: factory()


def _dealership(db_session) -> Dealership:
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
    return dealership


def _customer(db_session, group_id):
    return create_customer(
        db_session, group_id=group_id,
        data=CustomerCreate(
            customer_type="individual", language="de", first_name="Didier", last_name="Perrin",
            emails=[CustomerEmailCreate(email_type="personal", email_address="didier@example.ch", is_primary=True)],
        ),
        actor_id=uuid.uuid4(),
    )


def _stock_contract(db_session, dealership_id, group_id):
    item = create_stock_item(
        db_session, tenant_id=dealership_id,
        data=StockItemCreate(vehicle_label="Seat Leon 1.5 eTSI FR DSG", condition=StockItemCondition.USED, vin="1HGCM82633A004352"),
        actor_id=uuid.uuid4(),
    )
    customer = _customer(db_session, group_id)
    offer = create_offer(db_session, tenant_id=dealership_id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=group_id,
        data=OfferUpdate(customer_id=customer.id, vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
        actor_id=uuid.uuid4(),
    )
    contract = create_contract(db_session, tenant_id=dealership_id, offer=offer, actor_id=uuid.uuid4())
    return contract, item, customer


def test_confirm_contract_reserves_the_stock_item(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()

    contract, item, _customer = _stock_contract(db_session, dealership.id, group_id)

    confirmed = confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))
    assert confirmed.status == ContractStatus.CONFIRMED
    assert confirmed.reservation_id is not None
    assert confirmed.signed_at is not None

    db_session.expire_all()
    refreshed_item = get_stock_item_or_404(db_session, dealership.id, item.id)
    assert refreshed_item.reservation_state == ReservationState.RESERVED
    assert refreshed_item.active_reservation_id == confirmed.reservation_id


def test_confirm_contract_emits_the_exact_expected_payload(db_session, engine):
    """The exact shape app.inventory.services.pipeline::
    handle_sales_contract_confirmed already reads (WP-7, frozen)."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()

    contract, _item, _customer = _stock_contract(db_session, dealership.id, group_id)

    confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))

    message = db_session.query(OutboxMessage).filter_by(
        aggregate_id=contract.id, event_type="sales.contract.confirmed"
    ).one()
    assert set(message.payload.keys()) == {"contractId", "vehicleSource", "manualConfiguration", "tradeIn"}
    assert message.payload["vehicleSource"] == "existing"
    assert message.payload["manualConfiguration"] is None
    assert message.payload["tradeIn"] is None


def test_confirm_contract_manual_configuration_payload(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=group_id,
        data=OfferUpdate(vehicle_source="manual", vehicle_label="Volkswagen Käfer", manual_vehicle_condition="used", manual_base_price=Decimal(18000)),
        actor_id=uuid.uuid4(),
    )
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())

    confirmed = confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))
    assert confirmed.reservation_id is None  # nothing to reserve — no real stock item exists yet

    message = db_session.query(OutboxMessage).filter_by(
        aggregate_id=contract.id, event_type="sales.contract.confirmed"
    ).one()
    assert message.payload["vehicleSource"] == "manual"
    assert message.payload["manualConfiguration"] == {"vehicleLabel": "Volkswagen Käfer", "condition": "used"}


def test_two_events_are_never_the_same_name(db_session, engine):
    """ADR-046 — sales.contract.confirmed at signature,
    sales.contract.invoice_requested at hand-off, never the same event."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()

    contract, _item, _customer = _stock_contract(db_session, dealership.id, group_id)
    confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))
    request_invoice(db_session, contract=contract, actor_id=uuid.uuid4())

    event_types = {
        m.event_type for m in db_session.query(OutboxMessage).filter_by(aggregate_id=contract.id).all()
    }
    assert {"sales.contract.created", "sales.contract.confirmed", "sales.contract.invoice_requested"} <= event_types


def test_request_invoice_refuses_before_confirmation(db_session):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()

    contract, _item, _customer = _stock_contract(db_session, dealership.id, group_id)
    with pytest.raises(ConflictError):
        request_invoice(db_session, contract=contract, actor_id=uuid.uuid4())


def test_confirmation_transaction_scope_is_separate_from_reserve(db_session, engine):
    """The actual thing distinguishing Pattern B from the shared-transaction
    anti-pattern: force the sales-side write to fail AFTER reserve()
    already committed, and prove — via a THIRD session's worth of direct
    querying — that the compensating release() ran.
    """

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()

    contract, item, _customer = _stock_contract(db_session, dealership.id, group_id)

    from unittest.mock import patch

    with (
        patch("app.sales.services.contract.upsert_deal_projection", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError),
    ):
        confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))

    db_session.rollback()
    db_session.expire_all()
    refreshed_item = get_stock_item_or_404(db_session, dealership.id, item.id)
    assert refreshed_item.reservation_state == ReservationState.NONE
    refreshed_contract = db_session.get(type(contract), contract.id)
    assert refreshed_contract.status == ContractStatus.PENDING


def test_credit_block_stops_the_contract(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contract, _item, customer = _stock_contract(db_session, dealership.id, group_id)
    set_credit_block(db_session, customer=customer, blocked=True, reason="Zahlungsverzug", actor_id=uuid.uuid4())

    with pytest.raises(ConflictError):
        confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))


def test_do_not_contact_also_stops_the_contract(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contract, _item, customer = _stock_contract(db_session, dealership.id, group_id)
    update_customer(
        db_session, customer=customer, data=CustomerUpdate(lifecycle_status="do_not_contact"), actor_id=uuid.uuid4()
    )

    with pytest.raises(ConflictError):
        confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))


def test_credit_block_does_not_stop_offer_creation_or_editing(db_session):
    """S-D19: "quoting a blocked customer is often how the block gets
    resolved" — only the CONTRACT is stopped."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    customer = _customer(db_session, group_id)
    set_credit_block(db_session, customer=customer, blocked=True, reason="Test", actor_id=uuid.uuid4())

    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    updated = update_offer(
        db_session, offer=offer, group_id=group_id, data=OfferUpdate(customer_id=customer.id), actor_id=uuid.uuid4()
    )
    assert updated.status == OfferStatus.DRAFT
    assert updated.customer_id == customer.id


def test_stock_item_purchased_consumer_sets_is_invoiceable(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()

    contract, item, _customer = _stock_contract(db_session, dealership.id, group_id)
    confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))
    assert contract.is_invoiceable is False

    handle_stock_item_purchased(db_session, tenant_id=dealership.id, stock_item_id=item.id)

    db_session.refresh(contract)
    assert contract.is_invoiceable is True

    # Idempotent — a second delivery of the same fact is a no-op.
    handle_stock_item_purchased(db_session, tenant_id=dealership.id, stock_item_id=item.id)
    db_session.refresh(contract)
    assert contract.is_invoiceable is True


def test_cancel_confirmed_contract_releases_the_reservation(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()

    contract, item, _customer = _stock_contract(db_session, dealership.id, group_id)
    confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))

    cancel_contract(db_session, contract=contract, reason="Kunde storniert.", actor_id=uuid.uuid4(), session_factory=_session_factory(engine))

    db_session.expire_all()
    refreshed_item = get_stock_item_or_404(db_session, dealership.id, item.id)
    assert refreshed_item.reservation_state == ReservationState.NONE
