"""WP-8 PR-1: SalesOffer core + numbering (S-D01)."""

import uuid

import pytest

from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.core.pagination import SortPageParams
from app.core.sorting import SortField
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.services.numbering import allocate_offer_number
from app.sales.services.offer import cancel_offer, create_offer, get_offer_or_404, list_offers


def test_create_offer_starts_as_a_bare_draft(db_session):
    """The reference prototype allocates a number and opens an empty draft
    before Kunde or Fahrzeug are chosen — no body is required to create.
    """

    tenant_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())

    assert offer.status == OfferStatus.DRAFT
    assert offer.offer_number == "O-000001"
    assert offer.customer_id is None
    assert offer.vehicle_label is None


def test_offer_number_increments_per_tenant(db_session):
    tenant_id = uuid.uuid4()
    a = allocate_offer_number(db_session, tenant_id)
    b = allocate_offer_number(db_session, tenant_id)
    assert a == "O-000001"
    assert b == "O-000002"


def test_offer_number_series_is_independent_per_tenant(db_session):
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    assert allocate_offer_number(db_session, tenant_a) == "O-000001"
    assert allocate_offer_number(db_session, tenant_b) == "O-000001"


def test_create_offer_publishes_outbox_event(db_session):
    tenant_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())

    messages = db_session.query(OutboxMessage).filter_by(aggregate_id=offer.id).all()
    assert len(messages) == 1
    assert messages[0].event_type == "sales.offer.created"
    assert messages[0].payload["offerNumber"] == offer.offer_number


def test_cancel_offer_sets_status_and_reason(db_session):
    tenant_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())

    cancelled = cancel_offer(db_session, offer=offer, reason="Kunde hat abgesagt.", actor_id=uuid.uuid4())

    assert cancelled.status == OfferStatus.CANCELLED
    assert cancelled.cancelled_reason == "Kunde hat abgesagt."


def test_cancel_offer_twice_conflicts(db_session):
    tenant_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    cancel_offer(db_session, offer=offer, reason="Erstens.", actor_id=uuid.uuid4())

    with pytest.raises(ConflictError):
        cancel_offer(db_session, offer=offer, reason="Zweitens.", actor_id=uuid.uuid4())


def test_get_offer_or_404_is_tenant_scoped(db_session):
    from app.core.errors import NotFoundError

    tenant_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())

    assert get_offer_or_404(db_session, tenant_id, offer.id).id == offer.id
    with pytest.raises(NotFoundError):
        get_offer_or_404(db_session, uuid.uuid4(), offer.id)


def test_list_offers_scoped_to_tenant(db_session):
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    create_offer(db_session, tenant_id=tenant_a, actor_id=uuid.uuid4())
    create_offer(db_session, tenant_id=tenant_a, actor_id=uuid.uuid4())
    create_offer(db_session, tenant_id=tenant_b, actor_id=uuid.uuid4())

    sort_fields = [SortField(api_name="updatedAt", column=SalesOffer.updated_at, direction="desc", nullable=False)]
    rows, _cursor, total, _is_estimate = list_offers(
        db_session, tenant_id=tenant_a, params=SortPageParams(limit=50, cursor=None, sort_fields=sort_fields)
    )
    assert total == 2
    assert len(rows) == 2
