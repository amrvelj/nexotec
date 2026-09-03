"""KAN-12 / PRD-Sales v2: "Copy Offer (new offerId, prefilled)" — the
part of the ticket that was a genuine, confirmed gap (the preview and
autosave complaints in the same ticket did not reproduce against the
real build/spec; see that Notion card's own comment).
"""

import uuid

from app.core.auth import AccessRole, create_access_token
from app.core.outbox_model import OutboxMessage
from app.sales.models.offer import OfferStatus
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.offer import copy_offer, create_offer, update_offer


def _token(role: AccessRole | None = None) -> str:
    tid = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({role}) if role else frozenset(), is_dealer_manager=False,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- service level -----------------------------------------------------------


def test_copy_offer_starts_a_new_lineage(db_session):
    tenant_id = uuid.uuid4()
    source = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    source = update_offer(
        db_session, offer=source, group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="manual", manual_vehicle_condition="used", manual_base_price="28900.00"),
        actor_id=uuid.uuid4(),
    )
    source.status = OfferStatus.OPEN  # a "pending" offer, per the PRD's own lifecycle table
    db_session.commit()

    copy = copy_offer(db_session, source=source, actor_id=uuid.uuid4())

    assert copy.id != source.id
    assert copy.offer_number != source.offer_number
    assert copy.version == 1
    assert copy.status == OfferStatus.DRAFT  # never the source's own status
    assert copy.copied_from_offer_id == source.id


def test_copy_offer_carries_over_configuration_fields(db_session):
    tenant_id = uuid.uuid4()
    source = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    source = update_offer(
        db_session, offer=source, group_id=uuid.uuid4(),
        data=OfferUpdate(
            vehicle_source="manual", vehicle_label="VW Golf 2.0 TDI",
            manual_vehicle_condition="used", manual_base_price="28900.00",
            discount_type="amount", discount_value="500.00",
            leasing_down_payment="2000.00", leasing_term_months=36, leasing_km_per_year=10000,
        ),
        actor_id=uuid.uuid4(),
    )

    copy = copy_offer(db_session, source=source, actor_id=uuid.uuid4())

    assert copy.vehicle_source == "manual"
    assert copy.manual_vehicle_condition == "used"
    assert copy.manual_base_price == source.manual_base_price
    assert copy.discount_type == "amount"
    assert copy.discount_value == source.discount_value
    assert copy.leasing_down_payment == source.leasing_down_payment
    assert copy.leasing_term_months == 36
    assert copy.leasing_km_per_year == 10000
    # Re-derived fresh, never copied verbatim from the source's own frozen
    # figures — see copy_offer's own docstring for why.
    assert copy.gross_price is not None


def test_copy_offer_never_carries_over_cancellation_state(db_session):
    tenant_id = uuid.uuid4()
    source = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    source.status = OfferStatus.CANCELLED
    source.cancelled_reason = "Customer changed their mind"
    db_session.commit()

    copy = copy_offer(db_session, source=source, actor_id=uuid.uuid4())

    assert copy.status == OfferStatus.DRAFT
    assert copy.cancelled_reason is None


def test_copy_offer_publishes_its_own_created_event(db_session):
    tenant_id = uuid.uuid4()
    source = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())

    copy = copy_offer(db_session, source=source, actor_id=uuid.uuid4())

    messages = db_session.query(OutboxMessage).filter_by(aggregate_id=copy.id).all()
    assert len(messages) == 1
    assert messages[0].event_type == "sales.offer.created"


# --- API level -----------------------------------------------------------


def test_copy_offer_endpoint(client):
    token = _token(role=AccessRole.SALES)
    created = client.post("/v1/sales/offers", headers=_bearer(token)).json()

    response = client.post(f"/v1/sales/offers/{created['id']}/copy", headers=_bearer(token))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] != created["id"]
    assert body["copiedFromOfferId"] == created["id"]
    assert body["status"] == "draft"
    assert body["version"] == 1


def test_copy_offer_endpoint_requires_write_capability(client):
    token = _token(role=AccessRole.SALES)
    created = client.post("/v1/sales/offers", headers=_bearer(token)).json()

    no_role_token = _token()
    response = client.post(f"/v1/sales/offers/{created['id']}/copy", headers=_bearer(no_role_token))
    assert response.status_code == 403, response.text


def test_copy_offer_endpoint_cross_tenant_is_404_not_403(client):
    owner = _token(role=AccessRole.SALES)
    other = _token(role=AccessRole.SALES)
    created = client.post("/v1/sales/offers", headers=_bearer(owner)).json()

    response = client.post(f"/v1/sales/offers/{created['id']}/copy", headers=_bearer(other))
    assert response.status_code == 404, response.text
