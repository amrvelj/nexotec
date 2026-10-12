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
from app.inventory.models.stock_item import ReservationState, StockItem
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.services.contract import confirm_contract, get_contract_or_404
from tests.test_sales_contract_edit import _customer_born_contract, _edit
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
