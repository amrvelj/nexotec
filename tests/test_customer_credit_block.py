"""WP-8 PR-6: Customer.credit_block (ADR-065/S-D19) — a genuinely new
concept, distinct from lifecycle_status == DO_NOT_CONTACT."""

import uuid

import pytest

from app.core.errors import ConflictError
from app.customer.models.customer import CustomerLifecycleStatus
from app.customer.schemas.customer import CustomerCreate, CustomerEmailCreate
from app.customer.services.customer import create_customer, set_credit_block


def _customer(db_session, group_id):
    return create_customer(
        db_session,
        group_id=group_id,
        data=CustomerCreate(
            customer_type="individual",
            language="de",
            first_name="Erich",
            last_name="Schneider",
            emails=[CustomerEmailCreate(email_type="personal", email_address="erich@example.ch", is_primary=True)],
        ),
        actor_id=uuid.uuid4(),
    )


def test_blocking_requires_a_reason(db_session):
    group_id = uuid.uuid4()
    customer = _customer(db_session, group_id)
    with pytest.raises(ConflictError):
        set_credit_block(db_session, customer=customer, blocked=True, reason=None, actor_id=uuid.uuid4())


def test_block_and_unblock(db_session):
    group_id = uuid.uuid4()
    customer = _customer(db_session, group_id)

    blocked = set_credit_block(
        db_session, customer=customer, blocked=True, reason="Zahlungsverzug", actor_id=uuid.uuid4()
    )
    assert blocked.credit_block is True
    assert blocked.credit_block_reason == "Zahlungsverzug"
    assert blocked.credit_blocked_at is not None

    unblocked = set_credit_block(db_session, customer=blocked, blocked=False, reason=None, actor_id=uuid.uuid4())
    assert unblocked.credit_block is False
    assert unblocked.credit_block_reason is None
    assert unblocked.credit_blocked_at is None


def test_credit_block_is_distinct_from_do_not_contact(db_session):
    """ADR-065: do-not-contact stops offer AND contract; credit block
    stops only the contract. They must be two independent flags."""

    group_id = uuid.uuid4()
    customer = _customer(db_session, group_id)
    set_credit_block(db_session, customer=customer, blocked=True, reason="Test", actor_id=uuid.uuid4())

    assert customer.lifecycle_status != CustomerLifecycleStatus.DO_NOT_CONTACT
    assert customer.credit_block is True
