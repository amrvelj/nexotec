"""WP-8 PR-1: SalesContract core — two creation paths (S-D01/S-D06)."""

import uuid

import pytest

from app.core.errors import ConflictError, NotFoundError
from app.sales.models.contract import ContractStatus
from app.sales.services.contract import cancel_contract, create_contract, get_contract_or_404
from app.sales.services.numbering import allocate_contract_number
from app.sales.services.offer import create_offer


def test_contract_status_has_four_values():
    """`invoiced` is a real value the reference prototype's grid shows,
    even though WP-8 emits no code path that sets it yet (finance, WP-9+).
    """

    assert {s.value for s in ContractStatus} == {"pending", "confirmed", "cancelled", "invoiced"}


def test_create_contract_directly_has_no_offer_lineage(db_session):
    """Confirmed live: a stock item's detail header offers "Vertrag
    erstellen" as its own primary action, with no prior offer required.
    """

    tenant_id = uuid.uuid4()
    contract = create_contract(db_session, tenant_id=tenant_id, offer=None, actor_id=uuid.uuid4())

    assert contract.contract_number == "C-000001"
    assert contract.offer_id is None
    assert contract.offer_number is None
    assert contract.status == ContractStatus.PENDING


def test_create_contract_from_offer_denormalizes_lineage(db_session):
    """Confirmed live: "C-001195 ← O-003216" — the contract carries the
    offer's number and working fields at the moment of creation.
    """

    tenant_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    offer.customer_id = customer_id
    offer.customer_label = "Ursula Vogt"
    offer.vehicle_label = "Mercedes-Benz GLC"
    db_session.commit()

    contract = create_contract(db_session, tenant_id=tenant_id, offer=offer, actor_id=uuid.uuid4())

    assert contract.offer_id == offer.id
    assert contract.offer_number == offer.offer_number
    assert contract.customer_id == customer_id
    assert contract.customer_label == "Ursula Vogt"
    assert contract.vehicle_label == "Mercedes-Benz GLC"


def test_offer_and_contract_number_series_are_independent(db_session):
    tenant_id = uuid.uuid4()
    assert allocate_contract_number(db_session, tenant_id) == "C-000001"
    from app.sales.services.numbering import allocate_offer_number

    assert allocate_offer_number(db_session, tenant_id) == "O-000001"


def test_cancel_contract_while_pending(db_session):
    tenant_id = uuid.uuid4()
    contract = create_contract(db_session, tenant_id=tenant_id, offer=None, actor_id=uuid.uuid4())

    cancelled = cancel_contract(db_session, contract=contract, reason="Kunde hat abgesagt.", actor_id=uuid.uuid4())
    assert cancelled.status == ContractStatus.CANCELLED


def test_cancel_contract_refuses_from_non_pending_status(db_session):
    """Cancellation from CONFIRMED (which must also release the stock
    reservation, ADR-047) is PR-6 scope — PR-1 only ships the
    pending-only path.
    """

    tenant_id = uuid.uuid4()
    contract = create_contract(db_session, tenant_id=tenant_id, offer=None, actor_id=uuid.uuid4())
    contract.status = ContractStatus.CONFIRMED
    db_session.commit()

    with pytest.raises(ConflictError):
        cancel_contract(db_session, contract=contract, reason="Zu spät.", actor_id=uuid.uuid4())


def test_get_contract_or_404_is_tenant_scoped(db_session):
    tenant_id = uuid.uuid4()
    contract = create_contract(db_session, tenant_id=tenant_id, offer=None, actor_id=uuid.uuid4())

    assert get_contract_or_404(db_session, tenant_id, contract.id).id == contract.id
    with pytest.raises(NotFoundError):
        get_contract_or_404(db_session, uuid.uuid4(), contract.id)
