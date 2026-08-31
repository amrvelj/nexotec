"""WP-8 PR-2: the container generation flow's autosave PATCH + server-
computed container completeness (FR-S-05, S-D03)."""

import uuid

import pytest

from app.core.errors import ConflictError
from app.customer.schemas.customer import CustomerCreate, CustomerEmailCreate
from app.customer.services.customer import create_customer
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.offer import cancel_offer, compute_offer_containers, create_offer, update_offer


def _customer(db_session, group_id):
    return create_customer(
        db_session,
        group_id=group_id,
        data=CustomerCreate(
            customer_type="individual",
            language="de",
            first_name="Ursula",
            last_name="Vogt",
            emails=[CustomerEmailCreate(email_type="personal", email_address="ursula.vogt@example.ch", is_primary=True)],
        ),
        actor_id=uuid.uuid4(),
    )


def test_containers_start_not_started_except_leasing(db_session):
    tenant_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())

    containers = compute_offer_containers(offer)
    by_id = {c.id: c for c in containers}

    assert by_id["customer"].requirement == "required"
    assert by_id["customer"].status == "not_started"
    assert by_id["vehicle"].requirement == "required"
    assert by_id["vehicle"].status == "not_started"
    assert by_id["pricing"].status == "not_started"
    assert by_id["trade_in"].requirement == "optional"
    assert by_id["leasing"].requirement == "optional"
    # S-D03 — never a real calculator; always its own genuine status, not
    # "complete" dressed up.
    assert by_id["leasing"].status == "placeholder"


def test_update_offer_resolves_customer_label_server_side(db_session):
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    customer = _customer(db_session, group_id)

    updated = update_offer(
        db_session, offer=offer, group_id=group_id, data=OfferUpdate(customer_id=customer.id), actor_id=uuid.uuid4()
    )

    assert updated.customer_id == customer.id
    assert updated.customer_label == "Ursula Vogt"
    assert updated.customer_denorm_refreshed_at is not None


def test_update_offer_manual_vehicle_marks_vehicle_container_complete(db_session):
    tenant_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())

    updated = update_offer(
        db_session,
        offer=offer,
        group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="manual", vehicle_label="Volkswagen Käfer", manual_vehicle_condition="used"),
        actor_id=uuid.uuid4(),
    )

    containers = {c.id: c for c in compute_offer_containers(updated)}
    assert containers["vehicle"].status == "complete"
    # Pricing becomes available (not yet "complete" — no fields to fill
    # until PR-3) the moment a vehicle exists.
    assert containers["pricing"].status == "in_progress"


def test_update_offer_refuses_once_no_longer_draft(db_session):
    tenant_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    cancel_offer(db_session, offer=offer, reason="Test", actor_id=uuid.uuid4())

    with pytest.raises(ConflictError):
        update_offer(
            db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(vehicle_label="X"), actor_id=uuid.uuid4()
        )


def test_update_offer_clears_customer_when_set_to_none(db_session):
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    customer = _customer(db_session, group_id)
    offer = update_offer(
        db_session, offer=offer, group_id=group_id, data=OfferUpdate(customer_id=customer.id), actor_id=uuid.uuid4()
    )

    offer = update_offer(
        db_session, offer=offer, group_id=group_id, data=OfferUpdate(customer_id=None), actor_id=uuid.uuid4()
    )
    assert offer.customer_id is None
    assert offer.customer_label is None


def test_leasing_inputs_are_never_computed(db_session):
    """S-D03 — free-text capture only; nothing derives a monthly rate from
    these three numbers."""

    tenant_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())

    updated = update_offer(
        db_session,
        offer=offer,
        group_id=uuid.uuid4(),
        data=OfferUpdate(leasing_down_payment=5000, leasing_term_months=36, leasing_km_per_year=10000),
        actor_id=uuid.uuid4(),
    )

    assert updated.leasing_down_payment == 5000
    assert updated.leasing_term_months == 36
    assert updated.leasing_km_per_year == 10000
    assert not hasattr(updated, "leasing_monthly_rate")
