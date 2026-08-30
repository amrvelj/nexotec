"""WP-7 PR-4: the reservation service (ADR-047)."""

import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.errors import ConflictError, NotFoundError
from app.core.uuid7 import uuid7
from app.inventory.models.stock_item import LifecycleStatus, ReservationState, StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.reservation import release, reserve
from app.inventory.services.stock_item import create_stock_item


def _make_item(db_session, tenant_id, **overrides):
    data = {"vehicle_label": "Škoda Octavia", "condition": StockItemCondition.NEW}
    data.update(overrides)
    return create_stock_item(db_session, tenant_id=tenant_id, data=StockItemCreate(**data), actor_id=uuid.uuid4())


def test_reserve_sets_reservation_state(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    assert item.lifecycle_status == LifecycleStatus.PIPELINE

    result = reserve(
        db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=uuid.uuid4(), idempotency_key="k1"
    )
    db_session.expire_all()
    refreshed = db_session.get(StockItem, item.id)
    assert refreshed.reservation_state == ReservationState.RESERVED
    assert str(refreshed.active_reservation_id) == result["reservationId"]
    # Reservation is allowed while pipeline (ADR-054) — no lifecycle change.
    assert refreshed.lifecycle_status == LifecycleStatus.PIPELINE


def test_second_reserve_on_same_item_is_409(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    reserve(db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=uuid.uuid4(), idempotency_key="k1")

    with pytest.raises(ConflictError):
        reserve(
            db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=uuid.uuid4(), idempotency_key="k2"
        )


def test_reserve_is_idempotent_by_key_same_payload_replays(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    contract_id = uuid.uuid4()

    first = reserve(
        db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=contract_id, idempotency_key="same-key"
    )
    second = reserve(
        db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=contract_id, idempotency_key="same-key"
    )
    assert first == second


def test_reserve_key_reuse_with_different_payload_is_409(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    reserve(
        db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=uuid.uuid4(), idempotency_key="reused"
    )
    with pytest.raises(ConflictError):
        reserve(
            db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=uuid.uuid4(), idempotency_key="reused"
        )


def test_release_clears_reservation_and_allows_re_reserve(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    result = reserve(db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=uuid.uuid4(), idempotency_key="k1")

    release(db_session, tenant_id=tenant_id, reservation_id=uuid.UUID(result["reservationId"]), idempotency_key="rk1")
    db_session.expire_all()
    refreshed = db_session.get(StockItem, item.id)
    assert refreshed.reservation_state == ReservationState.NONE
    assert refreshed.active_reservation_id is None

    # No longer conflicts — the slot is free again.
    reserve(db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=uuid.uuid4(), idempotency_key="k2")


def test_release_unknown_reservation_is_404(db_session):
    with pytest.raises(NotFoundError):
        release(db_session, tenant_id=uuid.uuid4(), reservation_id=uuid.uuid4(), idempotency_key="rk1")


def test_reserve_commits_independently_of_the_callers_own_transaction(db_session, engine):
    """The actual point of PR-4 (ADR-047 Pattern B): reserve()'s commit is
    NOT joined to whatever transaction a caller (a future Sales) has open.
    Simulated here by making another write in the SAME session after
    reserve() returns, then rolling that back — exactly what a caller
    would do if its OWN subsequent work failed. The reservation must
    survive that rollback, proven by reading it back through a completely
    fresh session/connection.
    """

    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)

    result = reserve(db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=uuid.uuid4(), idempotency_key="k1")

    # Simulate the caller's own subsequent, still-UNCOMMITTED write (a
    # future Sales contract row, in its own session in reality — a plain
    # add+flush here since create_stock_item itself commits, which would
    # defeat the point of this test), then a failure that rolls only the
    # caller's own transaction back — never reserve()'s, since that
    # already committed before returning.
    other_item = StockItem(
        id=uuid7(),
        tenant_id=tenant_id,
        stock_number="S-999999",
        vehicle_label="Some other car the caller was also touching",
        condition=StockItemCondition.NEW,
    )
    db_session.add(other_item)
    db_session.flush()
    db_session.rollback()

    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    fresh_session = session_factory()
    try:
        persisted = fresh_session.get(StockItem, item.id)
        assert persisted is not None
        assert persisted.reservation_state == ReservationState.RESERVED
        assert str(persisted.active_reservation_id) == result["reservationId"]

        # The caller's own (later, unrelated) write really was rolled back —
        # confirming the rollback in this test actually did something, so
        # the reservation's survival isn't a false positive from a no-op
        # rollback.
        assert fresh_session.get(StockItem, other_item.id) is None
    finally:
        fresh_session.close()
