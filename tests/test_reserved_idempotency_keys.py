"""KAN-264: a client's Idempotency-Key never takes a key the server writes
itself.

Client keys and internal keys share `idempotency_record`'s one primary key
`(idempotency_key, tenant_id)`. Before KAN-264 a user sending
`Idempotency-Key: sales.contract.confirm:<contract id>` on any keyed POST
stored a record under the key Sales later confirms that contract with, and
the confirmation failed with 409 (`find_cached_response`: same key, another
path). Anto's ruling (2026-10-12): a client key shaped like an internal one
(`<context>.<action>:…`) is refused with 422, the standard validation body;
the header stays optional, internal keys and stored rows are unchanged.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.idempotency_model import IdempotencyRecord
from app.core.idempotent_route import RESERVED_KEY_PATTERN, is_reserved_idempotency_key
from app.sales.models.contract import ContractStatus, SalesContract
from tests.test_sales_idempotency import (  # noqa: F401 — app_sessions_on_the_test_engine is a fixture
    _confirmable_contract,
    _headers,
    _token,
    _with_address,
    app_sessions_on_the_test_engine,
)
from tests.test_sales_lifecycle_reservation import _dealership


def _records(db_session) -> list[IdempotencyRecord]:
    db_session.expire_all()
    return list(db_session.scalars(select(IdempotencyRecord)))


@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
def test_a_client_key_shaped_like_the_confirmation_key_is_refused_and_the_contract_still_confirms(
    client, db_session
):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contract = _with_address(db_session, _confirmable_contract(db_session, dealership.id, group_id), group_id)
    token = _token(dealership.id, group_id)
    crafted = f"sales.contract.confirm:{contract.id}"

    refused = client.post("/v1/sales/offers", headers=_headers(token, crafted))

    assert refused.status_code == 422, refused.text
    body = refused.json()["error"]
    assert body["code"] == "unprocessable_entity"
    [error] = body["details"]["errors"]
    assert error["loc"] == ["header", "Idempotency-Key"]
    assert _records(db_session) == []

    confirmed = client.post(
        f"/v1/sales/contracts/{contract.id}/confirm", headers=_headers(token, **{"If-Match": str(contract.version)})
    )

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    db_session.expire_all()
    assert db_session.get(SalesContract, contract.id).status == ContractStatus.CONFIRMED


@pytest.mark.parametrize(
    "crafted",
    [
        "sales.contract.confirm:00000000-0000-0000-0000-000000000000",
        "sales.contract.confirm-compensate:a:b",
        "sales.contract.cancel:x",
        "inventory.reservation_sweep:x",
        "inventory.reserve:x",
        "platform.anything:",
        "compliance.audit.purge:x",
    ],
)
def test_every_reserved_shape_is_refused_on_a_keyed_post(client, db_session, crafted):
    refused = client.post("/v1/sales/offers", headers=_headers(_token(), crafted))

    assert refused.status_code == 422, refused.text
    assert _records(db_session) == []


@pytest.mark.parametrize(
    "client_key",
    [
        str(uuid.uuid4()),
        "abc:def",
        "my.key",
        "my.key:with-colon",  # `my` is not a context
        "Sales.contract.confirm:x",  # not lowercase: never an internal key, and a different key in the table
        "sales:contract",  # no action
        "sales.:x",  # empty action
        "sales.contract",  # no colon
        "salesforce.sync:x",  # a context name is a whole word
        "x-sales.contract.confirm:y",
    ],
)
def test_a_key_that_is_not_of_the_reserved_shape_still_works(client, db_session, client_key):
    token = _token()
    first = client.post("/v1/sales/offers", headers=_headers(token, client_key))
    second = client.post("/v1/sales/offers", headers=_headers(token, client_key))

    assert first.status_code == 201, first.text
    assert second.json() == first.json()  # replayed, as any key is
    assert [r.idempotency_key for r in _records(db_session)] == [client_key]


def test_no_key_at_all_is_still_fine(client):
    assert client.post("/v1/sales/offers", headers=_headers(_token())).status_code == 201


def test_the_header_stays_optional_and_says_what_it_refuses(client):
    spec = client.get("/openapi.json").json()
    [header] = [p for p in spec["paths"]["/v1/sales/offers"]["post"]["parameters"] if p["name"] == "Idempotency-Key"]

    assert header["required"] is False
    assert "<context>.<action>:" in header["description"]


def test_the_grammar_is_anchored_and_tight():
    assert RESERVED_KEY_PATTERN.pattern.startswith("^")
    assert is_reserved_idempotency_key("sales.contract.confirm:1")
    assert not is_reserved_idempotency_key(" sales.contract.confirm:1")
    assert not is_reserved_idempotency_key("sales.Contract.confirm:1")
