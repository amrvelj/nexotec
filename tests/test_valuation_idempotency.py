"""KAN-266 step 6 (valuation): every valuation POST honours Idempotency-Key.

Per route: the same key twice makes one record (or runs the action once) and
answers the same; the same key with a different request is a 409 for the
key. Mark-used has no body, so its 409 case is the same key on another
valuation. The mechanism itself is pinned in tests/test_idempotent_route.py.

Mark-used is an If-Match transition: it is retried with the If-Match it was
first sent with, which is stale after the first success, so without the
route class the retry is a 409 version conflict instead of the original
success.
"""

import uuid

from sqlalchemy import func, select

from app.core.outbox_model import OutboxMessage
from app.valuation.models.valuation import Valuation
from tests.test_valuation import _contract_carrying, _sales_principal


def _headers(token: str, key: str | None = None, **extra: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}", **extra}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _count(db_session, model, *where) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(model).where(*where))


def _assert_key_conflict(response, key: str) -> None:
    """A 409 for the reused key, not for some other conflict."""

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["idempotencyKey"] == key


def _twice(client, path: str, token: str, body: dict | None, **extra: str):
    key = str(uuid.uuid4())
    first = client.post(path, json=body, headers=_headers(token, key, **extra))
    second = client.post(path, json=body, headers=_headers(token, key, **extra))
    assert first.status_code in (200, 201), first.text
    assert second.status_code == first.status_code, second.text
    assert second.json() == first.json()
    return key, first.json()


def _valuation(client, token: str, final_offer: str = "8000.00") -> dict:
    response = client.post(
        "/v1/valuations", json={"finalOffer": final_offer, "source": "manual"}, headers=_headers(token)
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- POST /v1/valuations ------------------------------------------------------------


def test_create_valuation_twice_under_one_key_makes_one_valuation(client, db_session):
    token, tenant_id = _sales_principal()

    _twice(client, "/v1/valuations", token, {"finalOffer": "12000.00", "source": "manual"})

    assert _count(db_session, Valuation, Valuation.tenant_id == tenant_id) == 1


def test_create_valuation_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    token, tenant_id = _sales_principal()
    key, _ = _twice(client, "/v1/valuations", token, {"finalOffer": "12000.00", "source": "manual"})

    response = client.post(
        "/v1/valuations", json={"finalOffer": "11500.00", "source": "manual"}, headers=_headers(token, key)
    )

    _assert_key_conflict(response, key)
    assert _count(db_session, Valuation, Valuation.tenant_id == tenant_id) == 1


# --- POST /v1/valuations/{id}/mark-used (If-Match, no body) ----------------------------


def test_a_retried_mark_used_replays_its_success_instead_of_a_version_conflict(client, db_session):
    token, tenant_id = _sales_principal()
    valuation = _valuation(client, token)
    _contract_carrying(db_session, uuid.UUID(valuation["id"]), tenant_id=tenant_id, signed=True)

    _, used = _twice(
        client, f"/v1/valuations/{valuation['id']}/mark-used", token, None, **{"If-Match": str(valuation["version"])}
    )

    assert used["status"] == "used"
    assert used["version"] == valuation["version"] + 1
    assert _count(db_session, OutboxMessage, OutboxMessage.event_type == "valuation.used",
                  OutboxMessage.aggregate_id == uuid.UUID(valuation["id"])) == 1


def test_mark_used_with_a_reused_key_on_another_valuation_is_a_409(client, db_session):
    token, tenant_id = _sales_principal()
    first, other = _valuation(client, token), _valuation(client, token, "9000.00")
    for valuation in (first, other):
        _contract_carrying(db_session, uuid.UUID(valuation["id"]), tenant_id=tenant_id, signed=True)
    key, _ = _twice(
        client, f"/v1/valuations/{first['id']}/mark-used", token, None, **{"If-Match": str(first["version"])}
    )

    response = client.post(
        f"/v1/valuations/{other['id']}/mark-used", headers=_headers(token, key, **{"If-Match": str(other["version"])})
    )

    _assert_key_conflict(response, key)
    db_session.expire_all()
    assert db_session.get(Valuation, uuid.UUID(other["id"])).used_at is None
