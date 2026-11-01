"""KAN-253 (Anto, 2026-10-31, option C) — «Verwendung aufheben»: an explicit,
audited action, limited to the dealer manager, that clears a «Verwendet»
stamp no signed contract of the dealership stands behind. It covers both
cases the nightly check reports and cannot clear: a hand stamp from before
KAN-115, and a confirmation whose commit and compensation both failed and
whose contract was then cancelled (or is still pending). The stamp's KAN-115
rule is unchanged: a valuation a signed contract carries stays used.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.base import utcnow
from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.sales.models.contract import ContractStatus
from app.valuation import reconciliation as valuation_reconciliation
from app.valuation.models.valuation import Valuation
from app.valuation.services.valuation import derive_status, revert_use_by_hand
from tests.test_sales_lifecycle_reservation import _dealership
from tests.test_sales_trade_in_valuation_use import _valuation
from tests.test_trade_in_valuation_reconciliation import (
    USED_WITHOUT_SIGNED_CONTRACT,
    _contract,
    _findings,
    _stamped,
)
from tests.token_support import dealer_group_of


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


@pytest.mark.parametrize("status", [ContractStatus.PENDING, ContractStatus.CANCELLED])
def test_a_stamp_left_by_a_failed_confirmation_whose_contract_was_never_signed_is_cleared(db_session, status):
    """Case 2: the confirmation's commit and its compensation both failed, so
    the stamp stands with only an unsigned contract behind it — still pending,
    or cancelled since. No signed deal used the valuation."""

    dealership = _dealership(db_session)
    valuation = _stamped(db_session, _valuation(db_session, dealership.id, uuid.uuid4()), hours_ago=3)
    _contract(db_session, dealership.id, valuation.id, signed=False, status=status)

    cleared = revert_use_by_hand(db_session, valuation=valuation, actor_id=uuid.uuid4(), reason="Bestätigung gescheitert")

    assert cleared.used_at is None


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


def test_a_valuation_that_is_not_used_is_refused(db_session):
    dealership = _dealership(db_session)
    valuation = _valuation(db_session, dealership.id, uuid.uuid4())

    with pytest.raises(ConflictError) as exc:
        revert_use_by_hand(db_session, valuation=valuation, actor_id=uuid.uuid4(), reason="Nichts zu tun")

    assert exc.value.details["reason"] == "not_used"


# --- HTTP -----------------------------------------------------------------------


def _created(client, token: str) -> dict:
    response = client.post("/v1/valuations", json={"finalOffer": "700.00", "source": "manual"}, headers=_bearer(token))
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
