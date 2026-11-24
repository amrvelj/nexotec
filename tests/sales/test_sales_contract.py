"""WP-8 PR-1: SalesContract core — two creation paths (S-D01/S-D06). KAN-58
adds a third: a direct contract for a known contact, no offer at all."""

import uuid

import pytest
from pydantic import ValidationError

from app.contact.schemas.contact import ContactCreate, ContactEmailCreate, ContactUpdate
from app.contact.services.contact import create_contact, update_contact
from app.core.errors import ConflictError, NotFoundError
from app.sales.models.contract import ContractStatus
from app.sales.schemas.contract import ContractCreate
from app.sales.services.contract import cancel_contract, create_contract, get_contract_or_404
from app.sales.services.numbering import allocate_contract_number
from app.sales.services.offer import create_offer


def _contact(db_session, group_id, **overrides):
    data = {
        "contact_type": "individual",
        "language": "fr",
        "first_name": "Ursula",
        "last_name": "Vogt",
        "emails": [ContactEmailCreate(email_type="personal", email_address="ursula@example.ch", is_primary=True)],
    }
    data.update(overrides)
    return create_contact(db_session, group_id=group_id, data=ContactCreate(**data), actor_id=uuid.uuid4(), acting_dealership_id=None, dealership_id=uuid.uuid4())


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
    contact_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    offer.contact_id = contact_id
    offer.contact_label = "Ursula Vogt"
    offer.vehicle_label = "Mercedes-Benz GLC"
    db_session.commit()

    contract = create_contract(db_session, tenant_id=tenant_id, offer=offer, actor_id=uuid.uuid4())

    assert contract.offer_id == offer.id
    assert contract.offer_number == offer.offer_number
    assert contract.contact_id == contact_id
    assert contract.contact_label == "Ursula Vogt"
    assert contract.vehicle_label == "Mercedes-Benz GLC"


# --- KAN-58: the contact→contract entry point ("New contract" on the
# contact's row menu / 360 overflow / offers-and-contracts tab) ----------


def test_create_contract_directly_for_a_contact_denormalizes_label_and_language(db_session):
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    contact = _contact(db_session, group_id)

    contract = create_contract(
        db_session, tenant_id=tenant_id, offer=None, contact_id=contact.id, group_id=group_id, actor_id=uuid.uuid4()
    )

    assert contract.offer_id is None
    assert contract.offer_number is None
    assert contract.contact_id == contact.id
    assert contract.contact_label == "Ursula Vogt"
    assert contract.contact_language == "fr"
    assert contract.contact_denorm_refreshed_at is not None
    # no offer means no vehicle/pricing to carry across
    assert contract.vehicle_source is None
    assert contract.gross_price is None


def test_create_contract_for_a_do_not_contact_contact_is_refused(db_session):
    """ADR-065/FR-21 — do-not-contact stops both the offer and the
    contract; the same attach-time guard update_offer already applies."""

    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    contact = _contact(db_session, group_id)
    update_contact(
        db_session, contact=contact, data=ContactUpdate(lifecycle_status="do_not_contact"),
        actor_id=uuid.uuid4(), acting_dealership_id=None, dealership_id=uuid.uuid4(),
    )

    with pytest.raises(ConflictError):
        create_contract(
            db_session, tenant_id=tenant_id, offer=None, contact_id=contact.id, group_id=group_id, actor_id=uuid.uuid4()
        )


def test_create_contract_for_a_credit_blocked_contact_still_succeeds(db_session):
    """A credit block stops CONFIRM, not creation — same as an offer
    (S-D19): creating a contract commits nobody yet."""

    from app.contact.services.contact import set_credit_block

    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    contact = _contact(db_session, group_id)
    set_credit_block(db_session, contact=contact, blocked=True, reason="Zahlungsverzug", actor_id=uuid.uuid4(), acting_dealership_id=None)

    contract = create_contract(
        db_session, tenant_id=tenant_id, offer=None, contact_id=contact.id, group_id=group_id, actor_id=uuid.uuid4()
    )
    assert contract.contact_id == contact.id
    assert contract.status == ContractStatus.PENDING


def test_create_contract_contact_id_without_group_id_is_a_programmer_error(db_session):
    with pytest.raises(ValueError):
        create_contract(db_session, tenant_id=uuid.uuid4(), offer=None, contact_id=uuid.uuid4(), actor_id=uuid.uuid4())


def test_contract_create_schema_rejects_offer_and_contact_together():
    with pytest.raises(ValidationError):
        ContractCreate(offer_id=uuid.uuid4(), contact_id=uuid.uuid4())


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


def test_cancel_contract_refuses_from_a_terminal_status(db_session):
    """PR-6 widens cancellation to PENDING or CONFIRMED (releasing the
    stock reservation first) — only a genuinely terminal status (already
    cancelled or invoiced) refuses.
    """

    tenant_id = uuid.uuid4()
    contract = create_contract(db_session, tenant_id=tenant_id, offer=None, actor_id=uuid.uuid4())
    contract.status = ContractStatus.INVOICED
    db_session.commit()

    with pytest.raises(ConflictError):
        cancel_contract(db_session, contract=contract, reason="Zu spät.", actor_id=uuid.uuid4())


def test_get_contract_or_404_is_tenant_scoped(db_session):
    tenant_id = uuid.uuid4()
    contract = create_contract(db_session, tenant_id=tenant_id, offer=None, actor_id=uuid.uuid4())

    assert get_contract_or_404(db_session, tenant_id, contract.id).id == contract.id
    with pytest.raises(NotFoundError):
        get_contract_or_404(db_session, uuid.uuid4(), contract.id)
