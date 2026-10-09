"""WP-7 PR-4: the reservation service (ADR-047)."""

import os
import threading
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.errors import ConflictError, NotFoundError
from app.core.uuid7 import uuid7
from app.inventory.models.stock_item import LifecycleStatus, ReservationState, StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.reservation import release, reserve, reserve_for_contract
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


# --- KAN-114: a replayed key whose reservation was released since. The
# idempotency record has no TTL, so Sales' retry under its per-contract key
# was handed back the released reservation id and reserved nothing.
# reserve_for_contract (Sales' entry) answers from the item; reserve() keeps
# the stored-response replay under a caller's own key. The HTTP endpoint's
# replay is the route's since KAN-266 (tests/test_inventory_idempotency.py::
# test_a_late_retry_of_a_reserve_never_re_reserves_a_released_car).


def test_reserve_replay_after_release_returns_the_stored_response_and_reserves_nothing(db_session):
    """reserve()'s keyed contract: a late duplicate under the caller's key
    never re-reserves a car its caller has released since."""

    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    contract_id = uuid.uuid4()
    first = reserve(db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=contract_id, idempotency_key="k1")
    release(db_session, tenant_id=tenant_id, reservation_id=uuid.UUID(first["reservationId"]), idempotency_key="rk1")

    replay = reserve(db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=contract_id, idempotency_key="k1")

    assert replay == first
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).reservation_state == ReservationState.NONE


def test_reserve_for_contract_replay_after_release_reserves_the_item_again(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    contract_id = uuid.uuid4()
    first = reserve_for_contract(
        db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=contract_id, idempotency_key="k1"
    )
    release(db_session, tenant_id=tenant_id, reservation_id=uuid.UUID(first["reservationId"]), idempotency_key="rk1")

    second = reserve_for_contract(
        db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=contract_id, idempotency_key="k1"
    )

    assert second["reservationId"] != first["reservationId"]
    db_session.expire_all()
    refreshed = db_session.get(StockItem, item.id)
    assert refreshed.reservation_state == ReservationState.RESERVED
    assert refreshed.reserved_by_contract_id == contract_id
    assert str(refreshed.active_reservation_id) == second["reservationId"]


def test_reserve_for_contract_replay_returns_the_live_reservation(db_session):
    """The replay's new reservation is never recorded against the key, so a
    further call must find the contract's live reservation, not reserve
    twice or 409 against itself."""

    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    contract_id = uuid.uuid4()
    first = reserve_for_contract(
        db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=contract_id, idempotency_key="k1"
    )
    release(db_session, tenant_id=tenant_id, reservation_id=uuid.UUID(first["reservationId"]), idempotency_key="rk1")
    second = reserve_for_contract(
        db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=contract_id, idempotency_key="k1"
    )

    third = reserve_for_contract(
        db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=contract_id, idempotency_key="k1"
    )

    assert third == second
    assert third != first


def test_reserve_for_contract_replay_is_409_when_another_contract_holds_the_item(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    contract_id = uuid.uuid4()
    first = reserve_for_contract(
        db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=contract_id, idempotency_key="k1"
    )
    release(db_session, tenant_id=tenant_id, reservation_id=uuid.UUID(first["reservationId"]), idempotency_key="rk1")
    other_contract_id = uuid.uuid4()
    reserve(db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=other_contract_id, idempotency_key="k2")

    with pytest.raises(ConflictError):
        reserve_for_contract(
            db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=contract_id, idempotency_key="k1"
        )

    db_session.expire_all()
    assert db_session.get(StockItem, item.id).reserved_by_contract_id == other_contract_id


def test_reserve_for_contract_key_reuse_with_another_contract_is_409(db_session):
    """The key check, not the item check: the item is free again when the
    key is reused, so only the key can refuse."""

    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    first = reserve_for_contract(
        db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=uuid.uuid4(), idempotency_key="reused"
    )
    release(db_session, tenant_id=tenant_id, reservation_id=uuid.UUID(first["reservationId"]), idempotency_key="rk1")

    with pytest.raises(ConflictError) as refused:
        reserve_for_contract(
            db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=uuid.uuid4(), idempotency_key="reused"
        )

    assert refused.value.details == {"idempotencyKey": "reused"}
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).reservation_state == ReservationState.NONE


@pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="row-lock interleaving needs Postgres")
def test_reserve_for_contract_reads_the_key_under_the_row_lock(db_session, engine):
    """Attempt A reserves, stores the key and is compensated between attempt
    B's key lookup and B's row lock. Read before the lock, B would find no
    key, reserve, and store the key a second time (a unique violation, a
    500). Read under the lock, A cannot run in that gap: it waits for B."""

    from unittest.mock import patch

    import app.inventory.services.reservation as reservation_module

    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    item_id = item.id
    contract_id = uuid.uuid4()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    real_find = reservation_module.find_cached_response
    errors: list[BaseException] = []

    def attempt_a():
        session = factory()
        try:
            result = reserve_for_contract(
                session, tenant_id=tenant_id, stock_item_id=item_id, contract_id=contract_id, idempotency_key="k1"
            )
            release(session, tenant_id=tenant_id, reservation_id=uuid.UUID(result["reservationId"]), idempotency_key="a-comp")
        except BaseException as exc:  # noqa: BLE001 — collected and asserted empty below, never swallowed
            errors.append(exc)
        finally:
            session.close()

    thread = threading.Thread(target=attempt_a)

    def find_then_let_a_run(*args, **kwargs):
        found = real_find(*args, **kwargs)
        if thread.ident is None:  # B's lookup, not A's own
            thread.start()
            thread.join(timeout=1.0)
        return found

    session_b = factory()
    try:
        with patch.object(reservation_module, "find_cached_response", side_effect=find_then_let_a_run):
            reserve_for_contract(
                session_b, tenant_id=tenant_id, stock_item_id=item_id, contract_id=contract_id, idempotency_key="k1"
            )
    finally:
        session_b.close()
        thread.join(timeout=10.0)

    assert not thread.is_alive()
    assert errors == []
