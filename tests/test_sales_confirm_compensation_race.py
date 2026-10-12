"""KAN-192: two confirmations of one contract. The first fails its own
commit, ends its transaction (which releases the contract's row lock) and
then compensates; the second takes the lock in that gap, is handed the same
reservation (reserve_for_contract is idempotent by item and contract) and
commits CONFIRMED with it. The first one's compensation must not then
release it: a confirmed contract on a car that is no longer reserved is the
double sale ADR-054's reservation axis exists to prevent.

Row-lock interleaving needs Postgres; SQLite has no row locks.
"""

import os
import threading
import time
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

import app.sales.services.contract as contract_module
from app.core.errors import ConflictError
from app.inventory.models.stock_item import ReservationState, StockItem
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.services.contract import confirm_contract, get_contract_or_404
from tests.test_sales_contract_edit import (
    _customer_born_contract,
    _edit,
    _token,
    app_sessions_on_the_test_engine,  # noqa: F401 — fixture, used by name below
)
from tests.test_sales_lifecycle_reservation import _dealership
from tests.test_sales_offer_refinements import _stock_item

pytestmark = pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"), reason="row-lock interleaving needs Postgres"
)


def _blocked_on_a_lock_or_done(engine, other: threading.Thread, timeout: float = 10.0) -> str:
    """'blocked' once some backend of this database waits on a lock, 'done'
    if `other` finished first, 'timeout' otherwise."""

    deadline = time.monotonic() + timeout
    with engine.connect() as conn:
        while time.monotonic() < deadline:
            if not other.is_alive():
                return "done"
            waiting = conn.execute(
                text("SELECT count(*) FROM pg_stat_activity "
                     "WHERE datname = current_database() AND wait_event_type = 'Lock'")
            ).scalar_one()
            conn.rollback()
            if waiting:
                return "blocked"
            time.sleep(0.05)
    return "timeout"


def test_a_failed_confirmations_compensation_never_releases_what_a_concurrent_one_committed(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id, with_option=False)
    contract = _edit(
        db_session, _customer_born_contract(db_session, dealership.id, group_id),
        vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label,
    )
    contract_id, item_id = contract.id, item.id
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def session_factory():
        return factory()

    second: dict[str, object] = {}

    def second_confirmation() -> None:
        session = factory()
        try:
            held = get_contract_or_404(session, dealership.id, contract_id, for_update=True)
            second["status"] = confirm_contract(
                session, contract=held, group_id=group_id, actor_id=uuid.uuid4(), session_factory=session_factory,
            ).status
        except BaseException as exc:  # noqa: BLE001 — asserted below, never swallowed
            second["error"] = exc
        finally:
            session.close()

    other = threading.Thread(target=second_confirmation)
    seen: list[str] = []

    real_upsert = contract_module.upsert_deal_projection
    real_release = contract_module.release

    def upsert_failing_the_first_commit(*args, **kwargs):
        if other.ident is None:  # only the first confirmation, before the second exists
            raise RuntimeError("the first confirmation's commit fails")
        return real_upsert(*args, **kwargs)

    def release_after_letting_the_second_run(*args, **kwargs):
        if other.ident is None:
            other.start()
            seen.append(_blocked_on_a_lock_or_done(engine, other))
        return real_release(*args, **kwargs)

    first = factory()
    try:
        with (
            patch.object(contract_module, "upsert_deal_projection", side_effect=upsert_failing_the_first_commit),
            patch.object(contract_module, "release", side_effect=release_after_letting_the_second_run),
        ):
            held = get_contract_or_404(first, dealership.id, contract_id, for_update=True)
            with pytest.raises(RuntimeError):
                confirm_contract(
                    first, contract=held, group_id=group_id, actor_id=uuid.uuid4(), session_factory=session_factory,
                )
            other.join(10)
    finally:
        first.close()

    assert not other.is_alive()
    assert "error" not in second, second.get("error")
    assert second["status"] == ContractStatus.CONFIRMED
    db_session.expire_all()
    signed = db_session.get(SalesContract, contract_id)
    car = db_session.get(StockItem, item_id)
    assert signed.status == ContractStatus.CONFIRMED and signed.reservation_id is not None
    # The contract is confirmed only with its car reserved — by it.
    assert car.reservation_state == ReservationState.RESERVED
    assert car.active_reservation_id == signed.reservation_id
    # The second confirmation waited on the contract's lock, held by the
    # compensation, instead of committing in between.
    assert seen == ["blocked"]


def _stock_contract(db_session):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id, with_option=False)
    contract = _edit(
        db_session, _customer_born_contract(db_session, dealership.id, group_id),
        vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label,
    )
    return dealership.id, group_id, contract, item.id


def test_a_confirmation_queued_behind_a_failing_one_commits_and_the_compensation_undoes_nothing(db_session, engine):
    """The likelier order: the second confirmation is already waiting on the
    contract's row lock when the first one fails its commit. The first one's
    rollback hands the lock to the second, which is given the same
    reservation (reserve_for_contract is idempotent by item and contract)
    and commits CONFIRMED with it. The compensation then waits for the lock,
    finds the contract signed and releases nothing; without that check it
    released the reservation the signed contract points at."""

    dealership_id, group_id, contract, item_id = _stock_contract(db_session)
    contract_id = contract.id
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    second_holds_the_lock = threading.Event()
    first_sessions: list[object] = []

    def session_factory():
        return factory()

    def first_session_factory():
        # The first confirmation's sessions: its reservation, then the
        # compensation's guard. The guard waits until the second
        # confirmation holds the contract's lock, so the order the test is
        # about never depends on which waiter Postgres wakes first.
        first_sessions.append(None)
        if len(first_sessions) >= 2:
            assert second_holds_the_lock.wait(10), "the second confirmation never took the contract's lock"
        return factory()

    second: dict[str, object] = {}

    def second_confirmation() -> None:
        session = factory()
        try:
            held = get_contract_or_404(session, dealership_id, contract_id, for_update=True)
            second_holds_the_lock.set()
            second["contract"] = confirm_contract(
                session, contract=held, group_id=group_id, actor_id=uuid.uuid4(), session_factory=session_factory,
            )
        except BaseException as exc:  # noqa: BLE001 — asserted below, never swallowed
            second["error"] = exc
        finally:
            session.close()

    other = threading.Thread(target=second_confirmation)
    seen: list[str] = []
    released: list[object] = []
    real_upsert = contract_module.upsert_deal_projection
    real_release = contract_module.release

    def upsert_failing_once_the_second_waits(*args, **kwargs):
        if other.ident is None:
            other.start()
            seen.append(_blocked_on_a_lock_or_done(engine, other))
            raise RuntimeError("the first confirmation's commit fails")
        return real_upsert(*args, **kwargs)

    def recording_release(*args, **kwargs):
        released.append(kwargs.get("reservation_id"))
        return real_release(*args, **kwargs)

    first = factory()
    try:
        with (
            patch.object(contract_module, "upsert_deal_projection", side_effect=upsert_failing_once_the_second_waits),
            patch.object(contract_module, "release", side_effect=recording_release),
        ):
            held = get_contract_or_404(first, dealership_id, contract_id, for_update=True)
            with pytest.raises(RuntimeError):
                confirm_contract(
                    first, contract=held, group_id=group_id, actor_id=uuid.uuid4(),
                    session_factory=first_session_factory,
                )
            other.join(10)
    finally:
        second_holds_the_lock.set()
        first.close()

    assert seen == ["blocked"]  # the second confirmation was queued on the lock before the first failed
    assert not other.is_alive()
    assert "error" not in second, second.get("error")
    assert released == []  # the compensation found the contract signed and undid nothing

    db_session.expire_all()
    signed = db_session.get(SalesContract, contract_id)
    car = db_session.get(StockItem, item_id)
    assert signed.status == ContractStatus.CONFIRMED and signed.reservation_id is not None
    assert car.reservation_state == ReservationState.RESERVED
    assert car.active_reservation_id == signed.reservation_id


@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
def test_a_refused_reservation_through_the_route_answers_at_once(client, db_session):
    """The route holds the contract's row lock; the compensation takes it on
    a session of its own. So the reserve-refusal path ends the request's
    transaction before compensating — without that, the compensation waited
    on its own request's lock for ever (Postgres cannot see that wait as a
    deadlock) and the request never answered."""

    dealership_id, group_id, contract, _item_id = _stock_contract(db_session)
    headers = {**_token(dealership_id, group_id), "If-Match": str(contract.version)}
    result: dict[str, object] = {}

    def refused(*args, **kwargs):
        raise ConflictError("Reservation refused.", details={"reason": "already_reserved"})

    def confirm() -> None:
        result["response"] = client.post(f"/v1/sales/contracts/{contract.id}/confirm", headers=headers)

    with patch.object(contract_module, "reserve_for_contract", side_effect=refused):
        sender = threading.Thread(target=confirm, daemon=True)
        sender.start()
        sender.join(10)

    assert not sender.is_alive(), "the confirmation hung on its own contract lock"
    response = result["response"]
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "already_reserved"
    db_session.expire_all()
    assert db_session.get(SalesContract, contract.id).status == ContractStatus.PENDING


@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
def test_two_confirmations_of_a_car_held_elsewhere_are_both_refused_promptly(client, db_session, engine):
    """A double submit on a car another contract holds: the first request's
    reservation is refused while the second is queued on the contract's
    lock. The refused reservation still held the stock item's row lock, so
    compensating before ending it left the first request's guard waiting on
    the second (which held the contract and waited on the item) and the
    second waiting on the first — a wait Postgres cannot see, so both hung
    for ever. Both must answer 409."""

    from tests.test_sales_lifecycle_reservation import _session_factory

    dealership_id, group_id, contract, item_id = _stock_contract(db_session)
    holder = _edit(
        db_session, _customer_born_contract(db_session, dealership_id, group_id),
        vehicle_source="stock", stock_item_id=item_id, vehicle_label="VW Golf GTI",
    )
    confirm_contract(
        db_session, contract=holder, group_id=group_id, actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )
    headers = {**_token(dealership_id, group_id), "If-Match": str(contract.version)}
    responses: dict[str, object] = {}

    def confirm(name: str) -> None:
        responses[name] = client.post(f"/v1/sales/contracts/{contract.id}/confirm", headers=headers)

    second = threading.Thread(target=confirm, args=("second",), daemon=True)
    seen: list[str] = []
    real_reserve = contract_module.reserve_for_contract

    def reserve_once_the_second_is_queued(*args, **kwargs):
        if second.ident is None:
            second.start()
            seen.append(_blocked_on_a_lock_or_done(engine, second))
        return real_reserve(*args, **kwargs)

    with patch.object(contract_module, "reserve_for_contract", side_effect=reserve_once_the_second_is_queued):
        first = threading.Thread(target=confirm, args=("first",), daemon=True)
        first.start()
        first.join(15)
        if second.ident is not None:
            second.join(15)

    assert seen == ["blocked"]  # the second request was queued on the contract's lock
    assert not first.is_alive() and not second.is_alive(), "the two confirmations hung on each other"
    assert responses["first"].status_code == 409, responses["first"].text
    assert responses["second"].status_code == 409, responses["second"].text
    db_session.expire_all()
    assert db_session.get(SalesContract, contract.id).status == ContractStatus.PENDING
    car = db_session.get(StockItem, item_id)
    assert car.active_reservation_id == db_session.get(SalesContract, holder.id).reservation_id
