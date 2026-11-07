"""KAN-253 (Anto, 2026-10-31, option C) — «Verwendung aufheben»: an explicit,
audited action, limited to the dealer manager, that clears a «Verwendet»
stamp no signed contract of the dealership stands behind. It covers both
cases the nightly check reports and cannot clear: a hand stamp from before
KAN-115, and a confirmation whose commit and compensation both failed and
whose contract was then cancelled. The stamp's KAN-115 rule is unchanged: a
valuation a signed contract carries stays used. KAN-520 (Anto, 2026-11-03,
option A): a pending contract carrying it refuses the clear too.
"""

import datetime as dt
import os
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.base import utcnow
from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.sales import reconciliation as sales_reconciliation
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.services.contract import confirm_contract
from app.valuation import reconciliation as valuation_reconciliation
from app.valuation.models.valuation import Valuation
from app.valuation.services import valuation as valuation_module
from app.valuation.services.valuation import derive_status, revert_use_by_hand
from tests.test_sales_lifecycle_reservation import _dealership, _session_factory
from tests.test_sales_trade_in_valuation_use import _trade_in_contract, _valuation
from tests.test_trade_in_valuation_reconciliation import (
    SIGNED_WITHOUT_USED_VALUATION,
    USED_WITHOUT_SIGNED_CONTRACT,
    _contract,
    _findings,
    _stamped,
)
from tests.token_support import dealer_group_of
from tests.vin_support import unique_vin


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _token(tenant_id: uuid.UUID, *, manager: bool, roles=frozenset({AccessRole.SALES})) -> tuple[str, uuid.UUID]:
    user_id = uuid.uuid4()
    token = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        group_id=dealer_group_of(str(tenant_id)),
        roles=roles,
        is_dealer_manager=manager,
    )
    return token, user_id


# --- service --------------------------------------------------------------------


def test_a_hand_stamp_no_signed_contract_carries_is_cleared_audited_and_published(db_session):
    """Case 1: a stamp set by hand before KAN-115. The nightly check reports
    it until the manager clears it; the clear is audited with the reason and
    publishes `valuation.valuation.use_reverted`."""

    dealership = _dealership(db_session)
    valuation = _stamped(db_session, _valuation(db_session, dealership.id, uuid.uuid4()), hours_ago=48)
    assert len(_findings(valuation_reconciliation, db_session, USED_WITHOUT_SIGNED_CONTRACT)) == 1
    used_at, version = valuation.used_at, valuation.version
    actor = uuid.uuid4()

    cleared = revert_use_by_hand(db_session, valuation=valuation, actor_id=actor, reason="Stempel von Hand, kein Vertrag")

    assert cleared.used_at is None and cleared.version == version + 1
    assert derive_status(cleared) != "used"
    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == valuation.id, AuditEvent.action == "valuation_use_reverted_by_hand")
    ).one()
    assert audit.actor_id == actor and audit.tenant_id == dealership.id
    assert audit.reason == "Stempel von Hand, kein Vertrag"
    assert audit.before == {"usedAt": used_at.isoformat()} and audit.after == {"usedAt": None}
    events = db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.aggregate_id == valuation.id, OutboxMessage.event_type == "valuation.valuation.use_reverted"
        )
    ).all()
    assert len(events) == 1
    assert _findings(valuation_reconciliation, db_session, USED_WITHOUT_SIGNED_CONTRACT) == []


def test_a_stamp_left_by_a_failed_confirmation_whose_contract_was_cancelled_is_cleared(db_session):
    """Case 2: the confirmation's commit and its compensation both failed, so
    the stamp stands with only an unsigned contract behind it, cancelled
    since. No signed deal used the valuation."""

    dealership = _dealership(db_session)
    valuation = _stamped(db_session, _valuation(db_session, dealership.id, uuid.uuid4()), hours_ago=3)
    _contract(db_session, dealership.id, valuation.id, signed=False, status=ContractStatus.CANCELLED)

    cleared = revert_use_by_hand(db_session, valuation=valuation, actor_id=uuid.uuid4(), reason="Bestätigung gescheitert")

    assert cleared.used_at is None


def test_a_pending_contract_carrying_the_valuation_refuses_the_clear(db_session):
    """KAN-520 (Anto, 2026-11-03, option A): while a pending contract of this
    dealership carries the valuation, its confirmation may run at any moment
    and would find the stamp set — so the stamp stays until that contract is
    cancelled. Nothing is written."""

    dealership = _dealership(db_session)
    valuation = _stamped(db_session, _valuation(db_session, dealership.id, uuid.uuid4()), hours_ago=3)
    _contract(db_session, dealership.id, valuation.id, signed=False, status=ContractStatus.PENDING)

    with pytest.raises(ConflictError) as exc:
        revert_use_by_hand(db_session, valuation=valuation, actor_id=uuid.uuid4(), reason="Versuch")

    assert exc.value.details["reason"] == "pending_contract_carries_it"
    db_session.expire_all()
    assert db_session.get(Valuation, valuation.id).used_at is not None
    assert db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == valuation.id)).all() == []


def test_another_dealerships_pending_contract_does_not_refuse_the_clear(db_session):
    """Per dealership, as the signed-contract check is."""

    dealership = _dealership(db_session)
    valuation = _stamped(db_session, _valuation(db_session, dealership.id, uuid.uuid4()), hours_ago=3)
    _contract(db_session, uuid.uuid4(), valuation.id, signed=False, status=ContractStatus.PENDING)

    assert revert_use_by_hand(db_session, valuation=valuation, actor_id=None, reason="Fremder Vertrag").used_at is None


@pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="interleaves two real transactions: Postgres only")
@pytest.mark.parametrize(
    "read_name", ["valuations_carried_by_pending_contracts", "valuations_carried_by_signed_contracts"]
)
def test_a_signing_committed_between_the_clears_two_reads_never_lets_it_through(db_session, engine, read_name):
    """KAN-520, the narrower race (found by the review): the clear asks Sales
    twice, once for pending and once for signed contracts, in two statements
    under READ COMMITTED. A signing that commits right after either read
    must still be seen. A pending contract only ever moves to signed (with
    `signed_at`, which never clears) or to an unsigned cancel, so reading
    pending first and signed second sees it in one of the two; the other
    order lets it fall between them."""

    dealership = _dealership(db_session)
    valuation = _stamped(db_session, _valuation(db_session, dealership.id, uuid.uuid4()), hours_ago=3)
    contract = _contract(db_session, dealership.id, valuation.id, signed=False, status=ContractStatus.PENDING)
    contract_id = contract.id
    open_session = _session_factory(engine)

    real_read = getattr(valuation_module, read_name)

    def read_then_sign(*args, **kwargs):
        answer = real_read(*args, **kwargs)
        signer = open_session()
        try:
            signed = signer.get(SalesContract, contract_id)
            signed.status = ContractStatus.CONFIRMED
            signed.signed_at = utcnow()
            signer.commit()
        finally:
            signer.close()
        return answer

    manager_session = open_session()
    try:
        held = manager_session.get(Valuation, valuation.id)
        with patch.object(valuation_module, read_name, read_then_sign), pytest.raises(ConflictError) as exc:
            revert_use_by_hand(manager_session, valuation=held, actor_id=uuid.uuid4(), reason="Zwischendurch")
    finally:
        manager_session.close()

    assert exc.value.details["reason"] in {"pending_contract_carries_it", "signed_contract_carries_it"}
    db_session.expire_all()
    assert db_session.get(Valuation, valuation.id).used_at is not None


@pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="interleaves two real transactions: Postgres only")
def test_a_clear_interleaved_with_a_re_confirmation_never_leaves_a_signed_contract_unstamped(db_session, engine):
    """KAN-520, the race itself. A stamp older than the grace whose pending
    contract is confirmed again: the confirmation finds the stamp set, writes
    nothing and releases the valuation's lock before it reserves the car and
    signs. A manager's clear lands exactly there, on a session of its own.
    Before KAN-520 it passed every check and the contract signed on an
    unstamped valuation; now it is refused, and the contract signs on a
    stamped one."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)
    _stamped(db_session, valuation, hours_ago=3)
    valuation_id = valuation.id

    open_session = _session_factory(engine)
    calls = 0
    clear_outcome: list[object] = []

    def interleaving_factory():
        # confirm_contract's first short-lived session consumes the valuation,
        # its second reserves the car: the clear runs between the two.
        nonlocal calls
        calls += 1
        if calls == 2:
            manager_session = open_session()
            try:
                held = manager_session.get(Valuation, valuation_id)
                revert_use_by_hand(manager_session, valuation=held, actor_id=uuid.uuid4(), reason="Zwischendurch")
                clear_outcome.append("cleared")
            except ConflictError as exc:
                clear_outcome.append(exc.details["reason"])
            finally:
                manager_session.close()
        return open_session()

    signed = confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=interleaving_factory
    )

    assert calls == 2, "the clear must run between the consume and the reservation"
    assert clear_outcome == ["pending_contract_carries_it"]
    assert signed.status == ContractStatus.CONFIRMED and signed.signed_at is not None
    db_session.expire_all()
    assert db_session.get(Valuation, valuation_id).used_at is not None
    assert _findings(sales_reconciliation, db_session, SIGNED_WITHOUT_USED_VALUATION) == []


@pytest.mark.parametrize("status", [ContractStatus.CONFIRMED, ContractStatus.CANCELLED])
def test_a_valuation_a_signed_contract_carries_stays_used(db_session, status):
    """KAN-115 / ADR-066: a signed deal used it — cancelled since or not."""

    dealership = _dealership(db_session)
    valuation = _stamped(db_session, _valuation(db_session, dealership.id, uuid.uuid4()), hours_ago=3)
    _contract(db_session, dealership.id, valuation.id, signed=True, status=status)

    with pytest.raises(ConflictError) as exc:
        revert_use_by_hand(db_session, valuation=valuation, actor_id=uuid.uuid4(), reason="Versuch")

    assert exc.value.details["reason"] == "signed_contract_carries_it"
    db_session.expire_all()
    assert db_session.get(Valuation, valuation.id).used_at is not None
    assert db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == valuation.id)).all() == []


def test_another_dealerships_signed_contract_does_not_keep_it_used(db_session):
    """The check is per dealership, as the stamp's own rule is (KAN-115)."""

    dealership = _dealership(db_session)
    valuation = _stamped(db_session, _valuation(db_session, dealership.id, uuid.uuid4()), hours_ago=3)
    _contract(db_session, uuid.uuid4(), valuation.id, signed=True)

    assert revert_use_by_hand(db_session, valuation=valuation, actor_id=None, reason="Fremder Vertrag").used_at is None


def test_a_stamp_younger_than_the_confirmation_grace_is_refused(db_session):
    """A confirmation stamps first and signs its contract in a later commit
    (KAN-101): a fresh stamp may be a confirmation in progress, so only one
    past the grace the nightly check also waits for can be cleared."""

    dealership = _dealership(db_session)
    valuation = _stamped(db_session, _valuation(db_session, dealership.id, uuid.uuid4()), hours_ago=0.25)
    _contract(db_session, dealership.id, valuation.id, signed=False, status=ContractStatus.PENDING)

    with pytest.raises(ConflictError) as exc:
        revert_use_by_hand(db_session, valuation=valuation, actor_id=uuid.uuid4(), reason="Zu früh")

    assert exc.value.details["reason"] == "stamp_too_recent"
    db_session.expire_all()
    assert db_session.get(Valuation, valuation.id).used_at is not None


def test_a_valuation_that_is_not_used_is_refused(db_session):
    dealership = _dealership(db_session)
    valuation = _valuation(db_session, dealership.id, uuid.uuid4())

    with pytest.raises(ConflictError) as exc:
        revert_use_by_hand(db_session, valuation=valuation, actor_id=uuid.uuid4(), reason="Nichts zu tun")

    assert exc.value.details["reason"] == "valuation_not_used"


# --- HTTP -----------------------------------------------------------------------


def _created(client, token: str) -> dict:
    response = client.post("/v1/valuations", json={"vin": unique_vin(), "vehicleMake": "VW", "vehicleModel": "Golf", "finalOffer": "700.00", "source": "manual"}, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _stamp_row(db_session, valuation_id: str) -> int:
    valuation = db_session.get(Valuation, uuid.UUID(valuation_id))
    valuation.used_at = utcnow() - dt.timedelta(days=2)
    valuation.version += 1
    db_session.commit()
    return valuation.version


def test_the_manager_clears_the_stamp_over_http(client, db_session):
    tenant_id = uuid.uuid4()
    token, user_id = _token(tenant_id, manager=True)
    created = _created(client, token)
    version = _stamp_row(db_session, created["id"])

    response = client.post(
        f"/v1/valuations/{created['id']}/revert-use",
        json={"reason": "  Alter Handstempel  "},
        headers={**_bearer(token), "If-Match": str(version)},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] != "used"
    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "valuation_use_reverted_by_hand")
    ).one()
    assert (audit.actor_id, audit.reason) == (user_id, "Alter Handstempel")


@pytest.mark.parametrize("roles", [frozenset({AccessRole.SALES}), frozenset({AccessRole.INVENTORY})])
def test_a_salesperson_or_inventory_user_without_the_manager_flag_is_refused(client, db_session, roles):
    """Only the dealer manager (the matrix row `valuation_use_reversal`)."""

    tenant_id = uuid.uuid4()
    manager_token, _ = _token(tenant_id, manager=True)
    created = _created(client, manager_token)
    version = _stamp_row(db_session, created["id"])
    token, _ = _token(tenant_id, manager=False, roles=roles)

    response = client.post(
        f"/v1/valuations/{created['id']}/revert-use",
        json={"reason": "Versuch"},
        headers={**_bearer(token), "If-Match": str(version)},
    )

    assert response.status_code == 403, response.text
    db_session.expire_all()
    assert db_session.get(Valuation, uuid.UUID(created["id"])).used_at is not None


@pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * 501}])
def test_a_reason_is_required(client, db_session, body):
    tenant_id = uuid.uuid4()
    token, _ = _token(tenant_id, manager=True)
    created = _created(client, token)
    version = _stamp_row(db_session, created["id"])

    response = client.post(
        f"/v1/valuations/{created['id']}/revert-use", json=body, headers={**_bearer(token), "If-Match": str(version)}
    )

    assert response.status_code == 422, response.text


def test_a_signed_contract_refuses_over_http_with_its_reason(client, db_session):
    tenant_id = uuid.uuid4()
    token, _ = _token(tenant_id, manager=True)
    created = _created(client, token)
    version = _stamp_row(db_session, created["id"])
    _contract(db_session, tenant_id, uuid.UUID(created["id"]), signed=True)

    response = client.post(
        f"/v1/valuations/{created['id']}/revert-use",
        json={"reason": "Versuch"},
        headers={**_bearer(token), "If-Match": str(version)},
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "signed_contract_carries_it"


def test_another_dealerships_valuation_is_404(client, db_session):
    token, _ = _token(uuid.uuid4(), manager=True)
    created = _created(client, token)
    version = _stamp_row(db_session, created["id"])
    other, _ = _token(uuid.uuid4(), manager=True)

    response = client.post(
        f"/v1/valuations/{created['id']}/revert-use",
        json={"reason": "Versuch"},
        headers={**_bearer(other), "If-Match": str(version)},
    )

    assert response.status_code == 404, response.text


def test_a_stale_version_is_refused(client, db_session):
    tenant_id = uuid.uuid4()
    token, _ = _token(tenant_id, manager=True)
    created = _created(client, token)
    version = _stamp_row(db_session, created["id"])

    response = client.post(
        f"/v1/valuations/{created['id']}/revert-use",
        json={"reason": "Versuch"},
        headers={**_bearer(token), "If-Match": str(version - 1)},
    )

    assert response.status_code == 409, response.text
    db_session.expire_all()
    assert db_session.get(Valuation, uuid.UUID(created["id"])).used_at is not None
