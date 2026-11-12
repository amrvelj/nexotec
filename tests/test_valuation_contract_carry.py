"""KAN-631 (ADR-131): valuation answers «is this valuation carried by a pending
or a signed contract» from its own rows (`valuation_contract_carry`), set by
the calls Sales already makes — never by asking Sales back.

Every case here runs through Sales' real paths (contract from an offer,
attach, confirmation, cancel) and then asks valuation: its service, and the
valuation list and detail over HTTP, whose `hasSignedContract` marker and
«Verwendung aufheben» refusals must read as they did when valuation asked
Sales. Covered: pending, signed, reverted (a failed confirmation's
compensation, and the manager's clear) and cancelled (pending and signed);
the compensation of a Sales write that fails after the pending call; a
contract written behind valuation's back (not seen, and reported); and the
nightly reconciliation of a carry that disagrees with Sales.
"""

import datetime as dt
import logging
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.core.base import utcnow
from app.core.errors import ConflictError
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.services.contract import cancel_contract, create_contract
from app.sales.services.trade_in import attach_trade_in_valuation
from app.valuation import reconciliation as valuation_reconciliation
from app.valuation.models.contract_carry import ContractCarryState, ValuationContractCarry
from app.valuation.models.valuation import Valuation
from app.valuation.services.valuation import mark_used_by_hand, revert_use_by_hand
from tests.test_sales_lifecycle_reservation import _dealership, _session_factory
from tests.test_sales_trade_in_valuation_use import _confirm, _trade_in_contract, _valuation
from tests.test_trade_in_valuation_reconciliation import _findings
from tests.test_valuation_revert_use import _bearer, _token

CARRY_DISAGREES = "valuation_contract_carry state differs from its contract"
CARRY_MISSING = "valuation carried by a contract it holds no carry row for"


def _carries(db_session, valuation_id) -> dict[uuid.UUID, ContractCarryState]:
    db_session.expire_all()
    rows = db_session.scalars(
        select(ValuationContractCarry).where(ValuationContractCarry.valuation_id == valuation_id)
    ).all()
    return {row.contract_id: row.state for row in rows}


def _markers(client, tenant_id, valuation_id) -> tuple[bool, bool]:
    """`hasSignedContract` as the valuation list and the detail show it."""

    token, _ = _token(tenant_id, manager=True)
    listed = client.get("/v1/valuations", headers=_bearer(token))
    assert listed.status_code == 200, listed.text
    [row] = [item for item in listed.json()["items"] if item["id"] == str(valuation_id)]
    detail = client.get(f"/v1/valuations/{valuation_id}", headers=_bearer(token))
    assert detail.status_code == 200, detail.text
    return row["hasSignedContract"], detail.json()["hasSignedContract"]


def _stamp_aged(db_session, valuation_id, *, hours: float = 3) -> Valuation:
    db_session.expire_all()
    valuation = db_session.get(Valuation, valuation_id)
    valuation.used_at = utcnow() - dt.timedelta(hours=hours)
    db_session.commit()
    return valuation


def _age_rows(db_session, *, hours: float = 2) -> None:
    """Both sides last written `hours` ago — past the reconciliation grace."""

    past = utcnow() - dt.timedelta(hours=hours)
    for row in db_session.scalars(select(ValuationContractCarry)).all():
        row.updated_at = past
    for contract in db_session.scalars(select(SalesContract)).all():
        contract.updated_at = past
    db_session.commit()


# --- pending --------------------------------------------------------------------


def test_a_contract_from_an_offer_is_recorded_pending_and_blocks_the_clear(client, db_session):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)

    assert _carries(db_session, valuation.id) == {contract.id: ContractCarryState.PENDING}
    assert _markers(client, dealership.id, valuation.id) == (False, False)
    held = _stamp_aged(db_session, valuation.id)
    with pytest.raises(ConflictError) as exc:
        revert_use_by_hand(db_session, valuation=held, actor_id=None, reason="Versuch")
    assert exc.value.details["reason"] == "pending_contract_carries_it"
    with pytest.raises(ConflictError) as refused:
        mark_used_by_hand(db_session, valuation=_unstamped(db_session, valuation.id), actor_id=None)
    assert refused.value.details["reason"] == "no_signed_contract"


def _unstamped(db_session, valuation_id) -> Valuation:
    db_session.expire_all()
    valuation = db_session.get(Valuation, valuation_id)
    valuation.used_at = None
    db_session.commit()
    return valuation


def test_attaching_another_valuation_to_a_pending_contract_moves_the_carry(db_session):
    """The pending contract now carries the new valuation (recorded before
    the attach commits) and no longer the old one (released after)."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    first = _valuation(db_session, dealership.id, group_id)
    second = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, first)

    attach_trade_in_valuation(db_session, deal=contract, valuation_id=second.id, actor_id=None)

    assert _carries(db_session, first.id) == {contract.id: ContractCarryState.RELEASED}
    assert _carries(db_session, second.id) == {contract.id: ContractCarryState.PENDING}
    held = _stamp_aged(db_session, first.id)
    assert revert_use_by_hand(db_session, valuation=held, actor_id=None, reason="Ersetzt").used_at is None


# --- signed ---------------------------------------------------------------------


def test_a_confirmed_contract_is_recorded_signed_and_shows_on_the_list_and_detail(client, db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)

    _confirm(db_session, engine, contract, group_id)

    assert _carries(db_session, valuation.id) == {contract.id: ContractCarryState.SIGNED}
    row = db_session.scalars(select(ValuationContractCarry)).one()
    assert row.signed_at is not None and row.contract_label == contract.contract_number
    assert _markers(client, dealership.id, valuation.id) == (True, True)
    with pytest.raises(ConflictError) as exc:
        revert_use_by_hand(db_session, valuation=_stamp_aged(db_session, valuation.id), actor_id=None, reason="Nein")
    assert exc.value.details["reason"] == "signed_contract_carries_it"
    # «Als verwendet markieren» repairs a missing stamp on it.
    assert mark_used_by_hand(db_session, valuation=_unstamped(db_session, valuation.id), actor_id=None).used_at


def test_a_signed_contract_stays_signed_on_its_valuation_when_cancelled(client, db_session, engine):
    """ADR-066: cancelling a signed contract leaves its valuation used."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)
    signed = _confirm(db_session, engine, contract, group_id)

    cancel_contract(
        db_session, contract=signed, reason="Kunde zurückgetreten", actor_id=None, session_factory=_session_factory(engine)
    )

    assert _carries(db_session, valuation.id) == {contract.id: ContractCarryState.SIGNED}
    assert _markers(client, dealership.id, valuation.id) == (True, True)


# --- reverted -------------------------------------------------------------------


def test_a_failed_confirmation_reverts_the_stamp_and_leaves_the_carry_pending(client, db_session, engine):
    """The contract's own commit fails: the compensation reverts «used», and
    the contract — still pending — still carries the valuation."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)

    with (
        patch("app.sales.services.contract.upsert_deal_projection", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError, match="boom"),
    ):
        _confirm(db_session, engine, contract, group_id)

    db_session.expire_all()
    assert db_session.get(Valuation, valuation.id).used_at is None
    assert _carries(db_session, valuation.id) == {contract.id: ContractCarryState.PENDING}
    assert _markers(client, dealership.id, valuation.id) == (False, False)


def test_the_manager_clears_a_stamp_once_the_pending_contract_is_cancelled(client, db_session, engine):
    """A stamp a failed confirmation left behind (commit and compensation
    both failed): refused while the pending contract carries it, cleared once
    that contract is cancelled — «Verwendung aufheben» (KAN-253, KAN-520)."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)
    with (
        patch("app.sales.services.contract.upsert_deal_projection", side_effect=RuntimeError("boom")),
        patch("app.sales.services.contract_compensation.revert_valuation_use", side_effect=OSError("valuation unreachable")),
        pytest.raises(RuntimeError, match="boom"),
    ):
        _confirm(db_session, engine, contract, group_id)
    stamped = _stamp_aged(db_session, valuation.id)
    with pytest.raises(ConflictError) as exc:
        revert_use_by_hand(db_session, valuation=stamped, actor_id=None, reason="Zu früh")
    assert exc.value.details["reason"] == "pending_contract_carries_it"

    db_session.expire_all()
    cancel_contract(
        db_session, contract=db_session.get(SalesContract, contract.id), reason="Bestätigung gescheitert",
        actor_id=None, session_factory=_session_factory(engine),
    )

    assert _carries(db_session, valuation.id) == {contract.id: ContractCarryState.RELEASED}
    cleared = revert_use_by_hand(
        db_session, valuation=db_session.get(Valuation, valuation.id), actor_id=None, reason="Vertrag storniert"
    )
    assert cleared.used_at is None
    assert _markers(client, dealership.id, valuation.id) == (False, False)


# --- cancelled ------------------------------------------------------------------


def test_cancelling_a_pending_contract_releases_its_valuation(client, db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)

    cancel_contract(db_session, contract=contract, reason="Kein Interesse", actor_id=None, session_factory=_session_factory(engine))

    assert _carries(db_session, valuation.id) == {contract.id: ContractCarryState.RELEASED}
    assert _markers(client, dealership.id, valuation.id) == (False, False)
    assert revert_use_by_hand(
        db_session, valuation=_stamp_aged(db_session, valuation.id), actor_id=None, reason="Handstempel"
    ).used_at is None


# --- the calls' own failure modes -------------------------------------------------


def test_a_contract_whose_own_commit_fails_releases_the_pending_carry_it_made(db_session, engine):
    """The pending call commits before Sales' write; when that write then
    fails, the compensation releases the row the call made pending."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)
    cancel_contract(db_session, contract=contract, reason="frei", actor_id=None, session_factory=_session_factory(engine))
    db_session.expire_all()
    offer = contract.offer_id
    from app.sales.services.offer import get_offer_or_404

    with (
        patch.object(db_session, "commit", side_effect=RuntimeError("commit lost")),
        pytest.raises(RuntimeError, match="commit lost"),
    ):
        create_contract(
            db_session, tenant_id=dealership.id, offer=get_offer_or_404(db_session, dealership.id, offer),
            actor_id=None, session_factory=_session_factory(engine),
        )

    db_session.rollback()
    carries = _carries(db_session, valuation.id)
    assert set(carries.values()) == {ContractCarryState.RELEASED}, carries
    assert len(carries) == 2  # the cancelled contract's, and the never-committed one's


def test_a_refused_pending_call_refuses_the_sales_write(db_session):
    """Valuation unreachable before the commit: the contract is not written —
    a pending contract never exists without its carry."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)
    other = _valuation(db_session, dealership.id, group_id)

    with (
        patch(
            "app.sales.services.valuation_carry.record_contract_carry_pending",
            side_effect=OSError("valuation unreachable"),
        ),
        pytest.raises(OSError),
    ):
        attach_trade_in_valuation(db_session, deal=contract, valuation_id=other.id, actor_id=None)

    db_session.rollback()
    assert db_session.get(SalesContract, contract.id).trade_in_valuation_id == valuation.id
    assert _carries(db_session, other.id) == {}


def test_a_signing_valuation_was_not_told_of_is_reported_overnight(db_session, engine, caplog):
    """The call after the signing fails: the contract stands signed, the
    carry stays pending (the clear stays refused), the failure is logged,
    and the nightly reconciliation reports the disagreement."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)

    with (
        patch(
            "app.sales.services.valuation_carry.record_contract_carry_signed",
            side_effect=OSError("valuation unreachable"),
        ),
        caplog.at_level(logging.ERROR, logger="app.sales.services.valuation_carry"),
    ):
        signed = _confirm(db_session, engine, contract, group_id)

    assert signed.status == ContractStatus.CONFIRMED
    assert any(record.message == "valuation_contract_carry_failed" for record in caplog.records)
    assert _carries(db_session, valuation.id) == {contract.id: ContractCarryState.PENDING}
    assert _findings(valuation_reconciliation, db_session, CARRY_DISAGREES) == []  # within the grace
    _age_rows(db_session)
    [finding] = _findings(valuation_reconciliation, db_session, CARRY_DISAGREES)
    assert finding.source_table == "valuation_contract_carry"


# --- valuation's own data, and its reconciliation --------------------------------


def test_a_contract_written_behind_valuations_back_is_not_seen_and_is_reported(client, db_session):
    """Valuation no longer asks Sales: a signed contract it was never told of
    does not mark the valuation — the nightly check names the valuation."""

    dealership = _dealership(db_session)
    valuation = _valuation(db_session, dealership.id, uuid.uuid4())
    db_session.add(
        SalesContract(
            tenant_id=dealership.id, contract_number="C-999001", status=ContractStatus.CONFIRMED,
            trade_in_valuation_id=valuation.id, signed_at=utcnow() - dt.timedelta(days=3),
        )
    )
    db_session.commit()

    assert _markers(client, dealership.id, valuation.id) == (False, False)
    assert _findings(valuation_reconciliation, db_session, CARRY_MISSING) == []  # within the grace
    _age_rows(db_session)
    [finding] = _findings(valuation_reconciliation, db_session, CARRY_MISSING)
    assert (finding.source_table, finding.source_row_id) == ("valuation", valuation.id)


@pytest.mark.parametrize(
    ("state", "contract_status", "signed"),
    [
        (ContractCarryState.PENDING, ContractStatus.CANCELLED, False),  # a release that never arrived
        (ContractCarryState.SIGNED, ContractStatus.PENDING, False),  # signed while Sales says pending
        (ContractCarryState.RELEASED, ContractStatus.PENDING, False),  # released while still carried
        (ContractCarryState.RELEASED, ContractStatus.CONFIRMED, True),
    ],
)
def test_a_carry_that_disagrees_with_its_contract_is_reported(db_session, state, contract_status, signed):
    dealership = _dealership(db_session)
    valuation = _valuation(db_session, dealership.id, uuid.uuid4())
    contract = SalesContract(
        tenant_id=dealership.id, contract_number="C-999002", status=contract_status,
        trade_in_valuation_id=valuation.id, signed_at=utcnow() - dt.timedelta(days=3) if signed else None,
    )
    db_session.add(contract)
    db_session.flush()
    db_session.add(
        ValuationContractCarry(
            tenant_id=dealership.id, valuation_id=valuation.id, contract_id=contract.id, state=state,
            signed_at=utcnow() if state == ContractCarryState.SIGNED else None,
        )
    )
    db_session.commit()
    _age_rows(db_session)

    findings = _findings(valuation_reconciliation, db_session, CARRY_DISAGREES)

    assert [f.source_table for f in findings] == ["valuation_contract_carry"]


def test_carries_that_agree_with_sales_are_not_reported(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    pending_valuation = _valuation(db_session, dealership.id, group_id)
    signed_valuation = _valuation(db_session, dealership.id, group_id)
    released_valuation = _valuation(db_session, dealership.id, group_id)
    _trade_in_contract(db_session, dealership.id, group_id, pending_valuation)
    signed, _ = _trade_in_contract(db_session, dealership.id, group_id, signed_valuation)
    _confirm(db_session, engine, signed, group_id)
    released, _ = _trade_in_contract(db_session, dealership.id, group_id, released_valuation)
    cancel_contract(db_session, contract=released, reason="weg", actor_id=None, session_factory=_session_factory(engine))
    _age_rows(db_session)

    assert _findings(valuation_reconciliation, db_session, CARRY_DISAGREES) == []
    assert _findings(valuation_reconciliation, db_session, CARRY_MISSING) == []


def test_another_dealerships_contract_is_never_recorded(db_session):
    """ADR-029: a contract of another dealership naming this valuation is
    Sales' reconciliation finding (KAN-259), never a carry of this one."""

    from app.valuation.public import record_contract_carry_pending

    dealership = _dealership(db_session)
    valuation = _valuation(db_session, dealership.id, uuid.uuid4())

    assert record_contract_carry_pending(
        db_session, tenant_id=uuid.uuid4(), valuation_id=valuation.id, contract_id=uuid.uuid4(),
        contract_label="C-1", actor_id=None,
    ) is False
    assert _carries(db_session, valuation.id) == {}
