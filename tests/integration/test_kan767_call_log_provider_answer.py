"""KAN-767 — `integration_call_log` records what the provider answered: the
HTTP status of every attempt, the caller's `purpose` beside the provider's
operation (`capability`), and the provider's short error identifier (a json:api
`title`, an OAuth `error` code) — never its free prose, a URL, a header or a
token.

Everything runs the real, registered `auto_i_dat` adapter against the fake
authorization server and identAPI of `tests/identapi_support.py` over
`httpx.MockTransport` (no network, invented values), through
`gateway.call_capability` — the one place rows are written. The AI and mail
adapters' rows are covered beside their own tests (`test_ai_providers.py`,
`test_kan215_mail.py`).
"""

import uuid

import httpx
import pytest

from app.integration.adapters.auto_i_dat_ident import OP_CAPTION_CODE, OP_VEHICLECATALOG
from app.integration.adapters.autoi_oauth import OAUTH_DISCOVERY_OPERATION, OAUTH_TOKEN_OPERATION
from app.integration.errors import (
    ProviderNotEntitledError,
    ProviderRejectedError,
    ProviderTransportError,
    ProviderUserQuotaExceededError,
)
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.services import gateway, resilience
from tests import identapi_support as fx
from tests.identapi_support import PROBE_PATHS, FakeAutoi

_PURPOSE = "catalogue_variants"


@pytest.fixture(autouse=True)
def _fresh_adapter_state():
    yield
    fx.uninstall()
    for connection_id in list(resilience._CIRCUITS):
        resilience.reset_circuit(connection_id)


def _rows(db_session, connection) -> list[IntegrationCallLog]:
    db_session.expire_all()
    return (
        db_session.query(IntegrationCallLog)
        .filter_by(connection_id=connection.id)
        .order_by(IntegrationCallLog.created_at, IntegrationCallLog.id)
        .all()
    )


def _answers(db_session, connection) -> list[tuple]:
    return [
        (r.capability, r.purpose, r.http_status, r.provider_error, r.status) for r in _rows(db_session, connection)
    ]


def _call(db_session, s, operation, *, purpose: str = _PURPOSE):
    with gateway.call_capability(
        db_session, connection=s.connection, capability="vehicle_data", actor_id=s.user.id, purpose=purpose
    ) as adapter:
        return operation(adapter)


# --- the identAPI ------------------------------------------------------------------------


def test_a_refused_identapi_request_records_403_its_purpose_and_the_title(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=FakeAutoi(api_status={PROBE_PATHS["ident_basis"]: 403}))

    with pytest.raises(ProviderNotEntitledError):
        _call(db_session, s, lambda adapter: adapter.probe_ident_basis())

    assert _answers(db_session, s.connection) == [
        (OAUTH_DISCOVERY_OPERATION, _PURPOSE, 200, None, CallStatus.SUCCESS),
        (OAUTH_TOKEN_OPERATION, _PURPOSE, 200, None, CallStatus.SUCCESS),
        (OP_VEHICLECATALOG, _PURPOSE, 403, "Forbidden", CallStatus.ERROR),
    ]


def test_the_providers_detail_the_token_and_the_secret_are_never_stored(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=FakeAutoi(api_status={PROBE_PATHS["ident_basis"]: 403}))

    with pytest.raises(ProviderNotEntitledError):
        _call(db_session, s, lambda adapter: adapter.probe_ident_basis())

    stored = " ".join(
        str(value)
        for row in _rows(db_session, s.connection)
        for value in (row.capability, row.purpose, row.http_status, row.provider_error)
    )
    assert "licence does not include" not in stored  # the fake's json:api `detail`
    for token in s.fake.tokens_issued:
        assert token not in stored
    assert fx.CLIENT_SECRET not in stored and fx.CLIENT_ID not in stored
    assert "autoi.ch" not in stored  # no URL


def test_a_successful_request_records_200_and_no_error(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)

    _call(db_session, s, lambda adapter: adapter.fetch_code_captions("de"))

    assert _answers(db_session, s.connection)[-1] == (OP_CAPTION_CODE, _PURPOSE, 200, None, CallStatus.SUCCESS)


def test_a_retried_5xx_is_two_rows_each_with_its_own_status(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=FakeAutoi(api_fail_once={"/caption/code": 503}))

    _call(db_session, s, lambda adapter: adapter.fetch_code_captions("de"))

    assert _answers(db_session, s.connection)[-2:] == [
        (OP_CAPTION_CODE, _PURPOSE, 503, "Transient", CallStatus.ERROR),
        (OP_CAPTION_CODE, _PURPOSE, 200, None, CallStatus.SUCCESS),
    ]


def test_a_401_then_the_new_token_records_401_then_200(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)
    _call(db_session, s, lambda adapter: adapter.fetch_code_captions("de"))
    s.fake.expire_tokens_once = True

    _call(db_session, s, lambda adapter: adapter.fetch_code_captions("de"))

    assert _answers(db_session, s.connection)[-3:] == [
        (OP_CAPTION_CODE, _PURPOSE, 401, "Unauthorized", CallStatus.ERROR),
        (OAUTH_TOKEN_OPERATION, _PURPOSE, 200, None, CallStatus.SUCCESS),
        (OP_CAPTION_CODE, _PURPOSE, 200, None, CallStatus.SUCCESS),
    ]


def test_a_transport_failure_records_no_http_status(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=FakeAutoi(raise_on_api=httpx.ConnectError("refused")))

    with pytest.raises(ProviderTransportError):
        _call(db_session, s, lambda adapter: adapter.fetch_code_captions("de"))

    api_rows = [a for a in _answers(db_session, s.connection) if a[0] == OP_CAPTION_CODE]
    assert api_rows == [(OP_CAPTION_CODE, _PURPOSE, None, None, CallStatus.ERROR)] * 2  # retried once


def test_a_refused_login_records_the_oauth_error_code_only(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)
    s.secrets.values[(s.connection.id, "client_secret")] = "not-the-secret"

    with pytest.raises(ProviderRejectedError):
        _call(db_session, s, lambda adapter: adapter.fetch_code_captions("de"))

    assert _answers(db_session, s.connection)[-1] == (
        OAUTH_TOKEN_OPERATION, _PURPOSE, 401, "invalid_client", CallStatus.ERROR,
    )


def test_a_user_quota_refusal_records_its_code_never_its_description(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=FakeAutoi(token_status=400))

    with pytest.raises(ProviderUserQuotaExceededError):
        _call(db_session, s, lambda adapter: adapter.fetch_code_captions("de"))

    (token_row,) = [r for r in _rows(db_session, s.connection) if r.capability == OAUTH_TOKEN_OPERATION]
    assert (token_row.http_status, token_row.provider_error) == (400, "user_quota_exceeded")


def test_a_failing_discovery_records_its_status_on_each_attempt(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=FakeAutoi(discovery_status=503))

    with pytest.raises(ProviderTransportError):
        _call(db_session, s, lambda adapter: adapter.fetch_code_captions("de"))

    assert [(a[0], a[2]) for a in _answers(db_session, s.connection)] == [
        (OAUTH_DISCOVERY_OPERATION, 503), (OAUTH_DISCOVERY_OPERATION, 503),
    ]


def test_without_a_purpose_the_capability_is_the_purpose(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)

    with gateway.call_capability(
        db_session, connection=s.connection, capability="vehicle_data", actor_id=s.user.id
    ) as adapter:
        adapter.fetch_code_captions("de")

    assert {r.purpose for r in _rows(db_session, s.connection)} == {"vehicle_data"}


# --- rows the gateway writes for a block ---------------------------------------------------


def test_a_block_that_metered_nothing_records_its_purpose_without_a_status(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)

    with gateway.call_capability(
        db_session, connection=s.connection, capability="vehicle_data", actor_id=s.user.id, purpose=_PURPOSE
    ):
        pass  # nothing sent

    assert _answers(db_session, s.connection) == [("vehicle_data", _PURPOSE, None, None, CallStatus.SUCCESS)]


def test_the_demo_provider_records_its_purpose_and_no_http_status(db_session):
    from tests.integration.test_integration_gateway import _make_connection, _make_provider

    connection = _make_connection(db_session, _make_provider(db_session))
    with gateway.call_capability(db_session, connection=connection, capability="vehicle_data", purpose=_PURPOSE) as adapter:
        adapter.fetch_captions("de")

    rows = _rows(db_session, connection)
    assert rows and {(r.purpose, r.http_status, r.provider_error) for r in rows} == {(_PURPOSE, None, None)}


def test_provider_error_is_one_bounded_line_at_the_choke_point(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)

    log = gateway.record_call(
        db_session, connection=s.connection, capability="x", status=CallStatus.ERROR, duration_ms=1,
        purpose="p" * 80, http_status=418, provider_error="first line\nsecond\tline\x00 " + "y" * 500,
    )

    assert log.provider_error is not None and len(log.provider_error) == 120
    assert log.provider_error.startswith("first line second line y")
    assert "\n" not in log.provider_error and "\x00" not in log.provider_error
    assert log.purpose == "p" * 64
    assert gateway.record_call(
        db_session, connection=s.connection, capability="x", status=CallStatus.ERROR, duration_ms=1,
        provider_error=" \n\t ",
    ).provider_error is None


def test_a_json_api_error_object_list_is_read_too(db_session, monkeypatch):
    """json:api's own error shape (`errors[0].title`); which of the two the
    live identAPI sends is not verified yet."""

    from app.integration.adapters.auto_i_dat_ident import _json_api_title_text

    listed = httpx.Response(403, json={"errors": [{"status": "403", "title": "Forbidden", "detail": "prose"}]})
    top = httpx.Response(403, json={"title": " Forbidden ", "detail": "prose"})
    neither = httpx.Response(403, json={"detail": "prose only"})
    assert _json_api_title_text(listed) == "Forbidden"
    assert _json_api_title_text(top) == "Forbidden"
    assert _json_api_title_text(neither) is None
    assert _json_api_title_text(httpx.Response(403, text="<html>")) is None


def test_an_unrelated_connections_rows_are_untouched(db_session, monkeypatch):
    """No column is filled by guessing: a row written through `record_call`
    without the new arguments keeps them NULL."""

    s = fx.connected(db_session, monkeypatch)
    log = gateway.record_call(
        db_session, connection=s.connection, capability="x", status=CallStatus.SUCCESS, duration_ms=1,
        correlation_id=uuid.uuid4(),
    )
    assert (log.purpose, log.http_status, log.provider_error) == (None, None, None)


def test_a_mail_providers_error_identifier_is_a_code_never_prose():
    """ADR-191: what a mail provider puts under `error` reaches the call log
    only when it is an OAuth code (letters, digits, underscores); prose —
    which could echo a user's input — never does."""

    from app.integration.adapters.mail_providers import _HttpMailAdapter

    def code(body):
        return _HttpMailAdapter._oauth_error(httpx.Response(400, json=body))

    assert code({"error": "invalid_grant"}) == "invalid_grant"
    assert code({"error": "The grant for AB 123 456 expired"}) is None
    assert code({"error": {"code": "ErrorAccessDenied", "message": "prose"}}) is None
    assert code({"error": ""}) is None
    assert _HttpMailAdapter._oauth_error(httpx.Response(400, text="not json")) is None
