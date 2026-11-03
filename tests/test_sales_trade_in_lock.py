"""KAN-309, as KAN-189 left it: a contract trade-in save used to commit the
request session part-way (the owner/keeper allocations, each foreign write
its own transaction, ADR-047; the vehicle-mdm resolution on a session of its
own), which released the contract's row lock the route took, so a
confirmation or an edit could land in that gap. KAN-189 (Anto, 2026-11-01)
removed both — a trade-in creates no vehicle-mdm record and allocates no
party — so the save is one transaction again: it holds the contract's row
lock from the route's read to its own commit, and a confirmation or an edit
of the same contract waits for it. `set_trade_in` still re-locks and
re-checks before writing (`lock_editable`), which is now belt and braces.

Row-lock interleaving needs Postgres; SQLite has no row locks.
"""

import threading
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import app.sales.services.trade_in as trade_in_module
from app.sales.models.contract import ContractStatus, SalesContract
from tests.test_sales_contract_edit import (
    _customer_born_contract,
    _manual_car,
    _postgres_only_row_locks,
    _token,
    app_sessions_on_the_test_engine,  # noqa: F401 — fixture, used by name below
)
from tests.test_sales_lifecycle_reservation import _dealership


@_postgres_only_row_locks
@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
def test_a_trade_in_save_holds_the_contracts_row_lock_until_its_commit(client, db_session, engine):
    """The trade-in request is paused inside its vehicle lookup — the last
    point at which, before KAN-189, it had already committed part-way.
    Another session then asks for the contract's row lock without waiting:
    it must not get it (a confirmation or an edit would wait there). Once
    released, the save writes the trade-in in one commit (200)."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contract = _manual_car(db_session, _customer_born_contract(db_session, dealership.id, group_id))
    assert contract.customer_id is not None  # before KAN-189 this made the save allocate parties and commit
    version_before = contract.version
    headers = {**_token(dealership.id, group_id), "If-Match": str(version_before)}

    in_lookup = threading.Event()
    probed = threading.Event()
    real_match = trade_in_module.match_vehicle

    def match_then_wait(*args, **kwargs):
        result = real_match(*args, **kwargs)
        if not in_lookup.is_set():
            in_lookup.set()
            probed.wait(10)
        return result

    outcome: dict[str, object] = {}

    def save_trade_in() -> None:
        outcome["response"] = client.post(
            f"/v1/sales/contracts/{contract.id}/trade-in",
            json={"vin": "WVWZZZ1KZAW309309", "vehicleLabel": "Opel Corsa"},
            headers=headers,
        )

    with patch.object(trade_in_module, "match_vehicle", side_effect=match_then_wait):
        saver = threading.Thread(target=save_trade_in)
        saver.start()
        try:
            assert in_lookup.wait(10), "the trade-in save never reached its vehicle lookup"
            probe = Session(bind=engine)
            try:
                with pytest.raises(OperationalError) as refused:
                    probe.execute(
                        select(SalesContract.id).where(SalesContract.id == contract.id).with_for_update(nowait=True)
                    )
                assert "could not obtain lock" in str(refused.value)
                probe.rollback()
            finally:
                probe.close()
        finally:
            probed.set()
            saver.join(10)

    assert not saver.is_alive()
    response = outcome["response"]
    assert response.status_code == 200, response.text
    assert response.json()["tradeInVin"] == "WVWZZZ1KZAW309309"

    db_session.expire_all()
    after = db_session.get(SalesContract, contract.id)
    assert after.status == ContractStatus.PENDING
    assert after.trade_in_vin == "WVWZZZ1KZAW309309"
    assert after.version == version_before + 1
