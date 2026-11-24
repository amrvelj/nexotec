"""WP-8 PR-6: Contact.credit_block (ADR-065/S-D19) — a genuinely new
concept, distinct from lifecycle_status == DO_NOT_CONTACT."""

import uuid

import pytest

from app.core.errors import ConflictError
from app.contact.models.contact import ContactLifecycleStatus
from app.contact.schemas.contact import ContactCreate, ContactEmailCreate
from app.contact.services.contact import create_contact, set_credit_block


def _contact(db_session, group_id):
    return create_contact(
        db_session,
        group_id=group_id,
        data=ContactCreate(
            contact_type="individual",
            language="de",
            first_name="Erich",
            last_name="Schneider",
            emails=[ContactEmailCreate(email_type="personal", email_address="erich@example.ch", is_primary=True)],
        ),
        actor_id=uuid.uuid4(), acting_dealership_id=None,
        dealership_id=uuid.uuid4(),
    )


def test_blocking_requires_a_reason(db_session):
    group_id = uuid.uuid4()
    contact = _contact(db_session, group_id)
    with pytest.raises(ConflictError):
        set_credit_block(db_session, contact=contact, blocked=True, reason=None, actor_id=uuid.uuid4(), acting_dealership_id=None)


def test_block_and_unblock(db_session):
    group_id = uuid.uuid4()
    contact = _contact(db_session, group_id)

    blocked = set_credit_block(
        db_session, contact=contact, blocked=True, reason="Zahlungsverzug", actor_id=uuid.uuid4(), acting_dealership_id=None
    )
    assert blocked.credit_block is True
    assert blocked.credit_block_reason == "Zahlungsverzug"
    assert blocked.credit_blocked_at is not None

    unblocked = set_credit_block(db_session, contact=blocked, blocked=False, reason=None, actor_id=uuid.uuid4(), acting_dealership_id=None)
    assert unblocked.credit_block is False
    assert unblocked.credit_block_reason is None
    assert unblocked.credit_blocked_at is None


def test_credit_block_is_distinct_from_do_not_contact(db_session):
    """ADR-065: do-not-contact stops offer AND contract; credit block
    stops only the contract. They must be two independent flags."""

    group_id = uuid.uuid4()
    contact = _contact(db_session, group_id)
    set_credit_block(db_session, contact=contact, blocked=True, reason="Test", actor_id=uuid.uuid4(), acting_dealership_id=None)

    assert contact.lifecycle_status != ContactLifecycleStatus.DO_NOT_CONTACT
    assert contact.credit_block is True
