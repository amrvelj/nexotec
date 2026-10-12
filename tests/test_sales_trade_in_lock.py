"""KAN-309: a contract trade-in save commits the request session part-way
(create_or_get_vehicle_mdm for a new VIN, allocate_vehicle_party for the
owner and the keeper — each foreign write its own transaction, ADR-047), and
that commit releases the contract's row lock the route took. A confirmation
that lands in that gap must not be followed by the trade-in being written
onto the signed contract: set_trade_in locks the contract again after its
last foreign write and re-checks it before writing.

Row-lock interleaving needs Postgres; SQLite has no row locks.
"""

import threading
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.sales.services.trade_in as trade_in_module
from app.core.outbox_model import OutboxMessage
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.services.contract import confirm_contract, get_contract_or_404
from tests.test_sales_contract_edit import (
    _customer_born_contract,
    _manual_car,
    _postgres_only_row_locks,
    _token,
    app_sessions_on_the_test_engine,  # noqa: F401 — fixture, used by name below
)
from tests.test_sales_lifecycle_reservation import _dealership, _session_factory


@_postgres_only_row_locks
@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
def test_a_confirmation_between_a_trade_in_saves_commits_refuses_the_trade_in(client, db_session, engine):
    """The trade-in request is paused right after its first owner/keeper
    allocation has committed; a confirmation of the same contract runs to
    its commit in that gap. Before KAN-309 the trade-in request then carried
    on with its stale contract and wrote the trade-in, its payable and a new
    version onto the signed contract (200). Now it locks the contract again,
    sees it confirmed and answers 409 `contract_not_pending`."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contract = _manual_car(db_session, _customer_born_contract(db_session, dealership.id, group_id))
    assert contract.customer_id is not None  # so the trade-in allocates owner and keeper, and commits
    version_before = contract.version  # `contract` is the identity map's object: keep the number, not the row
    headers = {**_token(dealership.id, group_id), "If-Match": str(version_before)}
    events_before = db_session.scalar(
        select(func.count()).select_from(OutboxMessage).where(OutboxMessage.aggregate_id == contract.id)
    )

    allocation_committed = threading.Event()
    confirmation_done = threading.Event()
    real_allocate = trade_in_module.allocate_vehicle_party

    def allocate_then_let_a_confirmation_in(*args, **kwargs):
        party = real_allocate(*args, **kwargs)
        if not allocation_committed.is_set():
            allocation_committed.set()
            confirmation_done.wait(10)
        return party

    result: dict[str, object] = {}

    def save_trade_in() -> None:
        result["response"] = client.post(
            f"/v1/sales/contracts/{contract.id}/trade-in",
            json={"vin": "WVWZZZ1KZAW309309", "vehicleLabel": "Opel Corsa"},
            headers=headers,
        )

    with patch.object(trade_in_module, "allocate_vehicle_party", side_effect=allocate_then_let_a_confirmation_in):
        saver = threading.Thread(target=save_trade_in)
        saver.start()
        try:
            assert allocation_committed.wait(10), "the trade-in save never reached its first allocation"
            confirming = Session(bind=engine)
            try:
                held = get_contract_or_404(confirming, dealership.id, contract.id, for_update=True)
                confirm_contract(
                    confirming, contract=held, group_id=group_id, actor_id=uuid.uuid4(),
                    session_factory=_session_factory(engine),
                )
            finally:
                confirming.close()
        finally:
            confirmation_done.set()
            saver.join(10)

    assert not saver.is_alive()
    response = result["response"]
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "contract_not_pending"

    db_session.expire_all()
    after = db_session.get(SalesContract, contract.id)
    assert after.status == ContractStatus.CONFIRMED
    assert (after.trade_in_vehicle_id, after.trade_in_vin, after.trade_in_label) == (None, None, None)
    assert after.payable == after.gross_price
    # The confirmation's version, not one more from the trade-in.
    assert after.version == version_before + 1
    # The contract's only new event is the confirmation's.
    assert db_session.scalar(
        select(func.count()).select_from(OutboxMessage).where(OutboxMessage.aggregate_id == contract.id)
    ) == events_before + 1


@_postgres_only_row_locks
@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
def test_an_edit_between_a_trade_in_saves_commits_makes_the_trade_in_stale(client, db_session, engine):
    """The same gap with a plain edit of the still-pending contract: the
    trade-in's If-Match was read before that edit, so the save is refused as
    stale (409 with the current version) instead of overwriting the edit's
    version — If-Match keeps its meaning across the foreign commits."""

    from decimal import Decimal

    from app.sales.schemas.contract import ContractUpdate
    from app.sales.services.contract import update_contract

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contract = _manual_car(db_session, _customer_born_contract(db_session, dealership.id, group_id))
    version_before = contract.version  # `contract` is the identity map's object: keep the number, not the row
    headers = {**_token(dealership.id, group_id), "If-Match": str(version_before)}

    allocation_committed = threading.Event()
    edit_done = threading.Event()
    real_allocate = trade_in_module.allocate_vehicle_party

    def allocate_then_let_an_edit_in(*args, **kwargs):
        party = real_allocate(*args, **kwargs)
        if not allocation_committed.is_set():
            allocation_committed.set()
            edit_done.wait(10)
        return party

    result: dict[str, object] = {}

    def save_trade_in() -> None:
        result["response"] = client.post(
            f"/v1/sales/contracts/{contract.id}/trade-in",
            json={"vin": "WVWZZZ1KZAW309310", "vehicleLabel": "Opel Corsa"},
            headers=headers,
        )

    with patch.object(trade_in_module, "allocate_vehicle_party", side_effect=allocate_then_let_an_edit_in):
        saver = threading.Thread(target=save_trade_in)
        saver.start()
        try:
            assert allocation_committed.wait(10), "the trade-in save never reached its first allocation"
            editing = Session(bind=engine)
            try:
                held = get_contract_or_404(editing, dealership.id, contract.id, for_update=True)
                update_contract(
                    editing, contract=held,
                    data=ContractUpdate(discount_type="amount", discount_value=Decimal("1000.00")),
                    actor_id=uuid.uuid4(),
                )
            finally:
                editing.close()
        finally:
            edit_done.set()
            saver.join(10)

    assert not saver.is_alive()
    response = result["response"]
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["currentVersion"] == version_before + 1

    db_session.expire_all()
    after = db_session.get(SalesContract, contract.id)
    assert after.status == ContractStatus.PENDING
    assert after.trade_in_vin is None
    assert after.version == version_before + 1
