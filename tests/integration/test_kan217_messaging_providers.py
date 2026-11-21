"""KAN-217 PR-3: the Betrieb's own messaging providers — Twilio for SMS and
Meta's WhatsApp Cloud API for WhatsApp (Anto, 2026-11-14, Q2 B).

Each adapter's wire format (as the providers document it publicly), the
failure vocabulary and what it charges to the breaker, the call-log row (the
operation, the status, the provider's error code — never the number or the
text), the usage view, «Test connection», the tenant-only rule and
`services/messaging.send`'s choice of connection. Fake transports and
invented values only: no real account is called."""

import base64
import importlib.util
import json
import logging
import uuid
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select

from app.core.auth import create_access_token
from app.core.errors import ConflictError, UnprocessableEntityError
from app.integration.adapters import messaging_providers
from app.integration.adapters.messaging_base import (
    CHANNEL_PROVIDER,
    MESSAGING_CATEGORY,
    MESSAGING_PROVIDER_CODES,
    MESSAGING_SEND_CAPABILITY,
    MessagingChannel,
    SmsMessage,
    WhatsAppTemplateMessage,
    WhatsAppTextMessage,
)
from app.integration.adapters.messaging_providers import META_GRAPH_VERSION, MetaWhatsAppAdapter, TwilioSmsAdapter
from app.integration.errors import (
    ProviderConfigurationError,
    ProviderNotEntitledError,
    ProviderRejectedError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.connection import (
    ConnectionEnvironment,
    ConnectionScope,
    ConnectionStatus,
    IntegrationConnection,
)
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import gateway, messaging, resilience, secrets_backend
from tests.token_support import dealer_group_of

# Invented values in the providers' documented shapes.
_SECRET = "invented-secret-value-kan217"
_ACCOUNT_SID = "AC" + "0" * 31 + "1"
_API_KEY_SID = "SK" + "0" * 31 + "2"
_SERVICE_SID = "MG" + "0" * 31 + "3"
_PHONE_NUMBER_ID = "100000000000017"
_RECIPIENT = "+41790000017"
_BODY = "Ihre Offerte liegt bereit: Garage Beispiel AG"

_TWILIO_CONFIG = {"accountSid": _ACCOUNT_SID, "apiKeySid": _API_KEY_SID, "sender": _SERVICE_SID}
_META_CONFIG = {"phoneNumberId": _PHONE_NUMBER_ID}
_TWILIO_ACCEPTED = {"sid": "SM" + "0" * 31 + "9", "status": "accepted"}
_META_ACCEPTED = {
    "messaging_product": "whatsapp",
    "contacts": [{"input": _RECIPIENT, "wa_id": "41790000017"}],
    "messages": [{"id": "wamid.invented-kan217"}],
}


class Recorder:
    """A fake provider: records each request and answers what it is given."""

    def __init__(self, status: int = 200, body: object | None = None, raise_exc: Exception | None = None):
        self.status = status
        self.body = body
        self.raise_exc = raise_exc
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_exc is not None:
            raise self.raise_exc
        return httpx.Response(self.status, json=self.body)


@pytest.fixture
def fake_provider(monkeypatch):
    def install(recorder: Recorder) -> Recorder:
        monkeypatch.setattr(messaging_providers, "http_transport", lambda: httpx.MockTransport(recorder))
        return recorder

    monkeypatch.setattr(secrets_backend, "resolve_secret", lambda *, connection_id, slot: _SECRET)
    return install


def _provider(db_session, code: str) -> IntegrationProvider:
    existing = db_session.scalar(select(IntegrationProvider).where(IntegrationProvider.provider_code == code))
    if existing is not None:
        return existing
    slot = "api_key" if code == "twilio" else "access_token"
    keys = list(_TWILIO_CONFIG) if code == "twilio" else list(_META_CONFIG)
    provider = IntegrationProvider(
        provider_code=code, category=MESSAGING_CATEGORY, display_name=code, auth_type="api_key",
        required_secret_slots=[slot], required_config_keys=keys, capability_codes=[MESSAGING_SEND_CAPABILITY],
        supports_sandbox=False,
    )
    db_session.add(provider)
    db_session.flush()
    return provider


def _connection(db_session, code: str, config: dict | None = None, *, tenant_id=None, enabled=True):
    provider = _provider(db_session, code)
    connection = IntegrationConnection(
        provider_id=provider.id, scope=ConnectionScope.TENANT, tenant_id=tenant_id or uuid.uuid4(), display_name=code,
        environment=ConnectionEnvironment.PRODUCTION,
        config=config if config is not None else (_TWILIO_CONFIG if code == "twilio" else _META_CONFIG),
        enabled=enabled,
    )
    db_session.add(connection)
    db_session.commit()
    return connection


def _rows(db_session, connection):
    return db_session.scalars(
        select(IntegrationCallLog).where(IntegrationCallLog.connection_id == connection.id).order_by(
            IntegrationCallLog.created_at
        )
    ).all()


def _failures(connection) -> int:
    return resilience._circuit_for(connection.id).failure_count


def _send(db_session, connection, message):
    return messaging.send(db_session, tenant_id=connection.tenant_id, message=message, actor_id=None, purpose="offer")


# --- Twilio -------------------------------------------------------------------------


def test_twilio_sends_the_sms_through_the_messaging_service_with_the_api_key(db_session, fake_provider):
    recorder = fake_provider(Recorder(status=201, body=_TWILIO_ACCEPTED))
    connection = _connection(db_session, "twilio")

    sent = _send(db_session, connection, SmsMessage(to=_RECIPIENT, body=_BODY))

    request = recorder.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"https://api.twilio.com/2010-04-01/Accounts/{_ACCOUNT_SID}/Messages.json"
    expected = base64.b64encode(f"{_API_KEY_SID}:{_SECRET}".encode()).decode()
    assert request.headers["authorization"] == f"Basic {expected}"
    assert parse_qs(request.content.decode()) == {
        "To": [_RECIPIENT], "MessagingServiceSid": [_SERVICE_SID], "Body": [_BODY]
    }
    assert (sent.provider_message_id, sent.provider_status) == (_TWILIO_ACCEPTED["sid"], "accepted")
    rows = _rows(db_session, connection)
    assert [(r.capability, r.status, r.http_status, r.provider_error, r.purpose) for r in rows] == [
        ("messages_create", CallStatus.SUCCESS, 201, None, "offer")
    ]


@pytest.mark.parametrize("sender", ["+41440000017", "GarageBsp"])
def test_twilio_sends_from_a_number_or_a_sender_name(db_session, fake_provider, sender):
    recorder = fake_provider(Recorder(status=201, body={"sid": "SMx", "status": "queued"}))
    connection = _connection(db_session, "twilio", {**_TWILIO_CONFIG, "sender": sender})

    _send(db_session, connection, SmsMessage(to=_RECIPIENT, body=_BODY))

    form = parse_qs(recorder.requests[0].content.decode())
    assert form["From"] == [sender] and "MessagingServiceSid" not in form


def test_a_refused_api_key_is_ours_to_word_charges_the_breaker_and_keeps_only_the_code(db_session, fake_provider):
    fake_provider(Recorder(status=401, body={"code": 20003, "message": f"Authenticate {_SECRET}", "status": 401}))
    connection = _connection(db_session, "twilio")

    with pytest.raises(ProviderRejectedError) as refused:
        _send(db_session, connection, SmsMessage(to=_RECIPIENT, body=_BODY))

    assert _SECRET not in str(refused.value)
    assert _failures(connection) == 1
    (row,) = _rows(db_session, connection)
    assert (row.status, row.http_status, row.provider_error) == (CallStatus.ERROR, 401, "20003")
    resilience.reset_circuit(connection.id)


def test_a_refused_recipient_is_the_requests_answer_and_never_charges_the_breaker(db_session, fake_provider):
    fake_provider(Recorder(status=400, body={"code": 21211, "message": f"The 'To' number {_RECIPIENT} is not valid."}))
    connection = _connection(db_session, "twilio")

    with pytest.raises(ProviderRequestError) as refused:
        _send(db_session, connection, SmsMessage(to=_RECIPIENT, body=_BODY))

    assert _RECIPIENT not in str(refused.value) and "21211" in str(refused.value)
    assert _failures(connection) == 0
    (row,) = _rows(db_session, connection)
    assert (row.status, row.http_status, row.provider_error) == (CallStatus.ERROR, 400, "21211")


def test_a_timeout_is_one_timeout_row_never_retried(db_session, fake_provider):
    recorder = fake_provider(Recorder(raise_exc=httpx.ReadTimeout("slow")))
    connection = _connection(db_session, "twilio")

    with pytest.raises(ProviderTimeoutError):
        _send(db_session, connection, SmsMessage(to=_RECIPIENT, body=_BODY))

    assert len(recorder.requests) == 1  # a message is billed: never sent twice
    assert [r.status for r in _rows(db_session, connection)] == [CallStatus.TIMEOUT]
    assert _failures(connection) == 1
    resilience.reset_circuit(connection.id)


def test_five_provider_outages_open_the_breaker_of_that_connection_only(db_session, fake_provider):
    recorder = fake_provider(Recorder(status=503, body={}))
    connection = _connection(db_session, "twilio")
    other = _connection(db_session, "twilio")

    for _ in range(5):
        with pytest.raises(ProviderTransportError):
            _send(db_session, connection, SmsMessage(to=_RECIPIENT, body=_BODY))
    with pytest.raises(resilience.CircuitOpenError):
        _send(db_session, connection, SmsMessage(to=_RECIPIENT, body=_BODY))
    assert len(recorder.requests) == 5
    assert not resilience.is_circuit_open(other.id)
    resilience.reset_circuit(connection.id)


@pytest.mark.parametrize(
    "config",
    [
        {**_TWILIO_CONFIG, "accountSid": "AC../../Accounts"},
        {**_TWILIO_CONFIG, "apiKeySid": ""},
        {**_TWILIO_CONFIG, "sender": "Garage Beispiel AG Zürich"},
    ],
)
def test_a_malformed_configuration_is_the_dealers_to_fix_and_sends_nothing(db_session, fake_provider, config):
    recorder = fake_provider(Recorder(status=201, body=_TWILIO_ACCEPTED))
    connection = _connection(db_session, "twilio", config)

    with pytest.raises(ProviderConfigurationError):
        _send(db_session, connection, SmsMessage(to=_RECIPIENT, body=_BODY))

    assert recorder.requests == [] and _rows(db_session, connection) == []
    assert _failures(connection) == 0


# --- Meta WhatsApp Cloud API -------------------------------------------------------


def test_meta_sends_an_approved_template_with_its_language_and_parameters(db_session, fake_provider):
    recorder = fake_provider(Recorder(body=_META_ACCEPTED))
    connection = _connection(db_session, "meta_whatsapp")

    sent = _send(
        db_session,
        connection,
        WhatsAppTemplateMessage(to=_RECIPIENT, template_name="offer_sent", language="de", body_parameters=("Muster",)),
    )

    request = recorder.requests[0]
    assert str(request.url) == f"https://graph.facebook.com/{META_GRAPH_VERSION}/{_PHONE_NUMBER_ID}/messages"
    assert request.headers["authorization"] == f"Bearer {_SECRET}"
    assert json.loads(request.content) == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _RECIPIENT,
        "type": "template",
        "template": {
            "name": "offer_sent",
            "language": {"code": "de"},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": "Muster"}]}],
        },
    }
    assert sent.provider_message_id == "wamid.invented-kan217"
    assert [(r.capability, r.status, r.http_status) for r in _rows(db_session, connection)] == [
        ("messages", CallStatus.SUCCESS, 200)
    ]


def test_meta_sends_a_free_text_inside_the_window(db_session, fake_provider):
    recorder = fake_provider(Recorder(body=_META_ACCEPTED))
    connection = _connection(db_session, "meta_whatsapp")

    _send(db_session, connection, WhatsAppTextMessage(to=_RECIPIENT, body=_BODY))

    payload = json.loads(recorder.requests[0].content)
    assert payload["type"] == "text" and payload["text"] == {"preview_url": False, "body": _BODY}


@pytest.mark.parametrize(
    ("status", "code", "raised", "charged"),
    [
        (400, 131047, ProviderRequestError, 0),  # outside the 24-hour window: a template only
        (400, 190, ProviderRejectedError, 1),  # the token expired or was revoked
        (401, None, ProviderRejectedError, 1),
        (400, 131056, ProviderRejectedError, 1),  # a pair rate limit
        (403, 200, ProviderNotEntitledError, 0),  # the token may not use this number
        (400, 100, ProviderRequestError, 0),  # an invalid parameter
        (500, 1, ProviderTransportError, 1),
    ],
)
def test_metas_error_codes_map_to_our_vocabulary(db_session, fake_provider, status, code, raised, charged):
    error = {"message": f"(#{code}) about {_RECIPIENT}", "type": "OAuthException", "fbtrace_id": "Ainvented"}
    if code is not None:
        error["code"] = code
    fake_provider(Recorder(status=status, body={"error": error}))
    connection = _connection(db_session, "meta_whatsapp")

    with pytest.raises(raised) as refused:
        _send(db_session, connection, WhatsAppTextMessage(to=_RECIPIENT, body=_BODY))

    assert _RECIPIENT not in str(refused.value)
    assert _failures(connection) == charged
    (row,) = _rows(db_session, connection)
    assert (row.http_status, row.provider_error) == (status, str(code) if code is not None else None)
    resilience.reset_circuit(connection.id)


def test_a_phone_number_id_that_is_not_digits_never_reaches_a_url(db_session, fake_provider):
    recorder = fake_provider(Recorder(body=_META_ACCEPTED))
    connection = _connection(db_session, "meta_whatsapp", {"phoneNumberId": "1/../me"})

    with pytest.raises(ProviderConfigurationError):
        _send(db_session, connection, WhatsAppTextMessage(to=_RECIPIENT, body=_BODY))

    assert recorder.requests == []


# --- the message's shape and the channel -------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        SmsMessage(to="0790000017", body=_BODY),  # not E.164
        SmsMessage(to=_RECIPIENT, body=""),
        SmsMessage(to=_RECIPIENT, body="x" * 1601),
        WhatsAppTemplateMessage(to=_RECIPIENT, template_name="Offer Sent", language="de"),
        WhatsAppTemplateMessage(to=_RECIPIENT, template_name="offer_sent", language="deutsch"),
    ],
)
def test_a_malformed_message_is_refused_before_the_gateway(db_session, fake_provider, message):
    recorder = fake_provider(Recorder(body=_META_ACCEPTED))
    connection = _connection(db_session, CHANNEL_PROVIDER[message.channel])

    with pytest.raises(ValueError):
        _send(db_session, connection, message)

    assert recorder.requests == [] and _rows(db_session, connection) == []
    assert _failures(connection) == 0


def test_an_adapter_refuses_a_message_of_the_other_channel(db_session, fake_provider):
    recorder = fake_provider(Recorder(body=_META_ACCEPTED))
    twilio = _connection(db_session, "twilio")
    meta = _connection(db_session, "meta_whatsapp")

    with pytest.raises(TypeError):
        TwilioSmsAdapter(db_session, twilio, None, "t").send(WhatsAppTextMessage(to=_RECIPIENT, body=_BODY))
    with pytest.raises(TypeError):
        MetaWhatsAppAdapter(db_session, meta, None, "t").send(SmsMessage(to=_RECIPIENT, body=_BODY))
    assert recorder.requests == []


# --- which connection sends ---------------------------------------------------------


def test_send_uses_the_betriebs_own_connection_for_the_channel(db_session, fake_provider):
    recorder = fake_provider(Recorder(status=201, body=_TWILIO_ACCEPTED))
    tenant = uuid.uuid4()
    mine = _connection(db_session, "twilio", tenant_id=tenant)
    _connection(db_session, "twilio")  # another Betrieb's
    _connection(db_session, "meta_whatsapp", tenant_id=tenant)

    messaging.send(db_session, tenant_id=tenant, message=SmsMessage(to=_RECIPIENT, body=_BODY), actor_id=None, purpose="p")

    assert len(recorder.requests) == 1 and len(_rows(db_session, mine)) == 1
    assert messaging.connected_channels(db_session, tenant_id=tenant) == frozenset(MessagingChannel)


@pytest.mark.parametrize("state", ["none", "disabled", "other_channel"])
def test_without_an_enabled_connection_for_the_channel_the_send_is_a_409(db_session, fake_provider, state):
    recorder = fake_provider(Recorder(status=201, body=_TWILIO_ACCEPTED))
    tenant = uuid.uuid4()
    if state == "disabled":
        _connection(db_session, "twilio", tenant_id=tenant, enabled=False)
    if state == "other_channel":
        _connection(db_session, "meta_whatsapp", tenant_id=tenant)

    with pytest.raises(ConflictError) as refused:
        messaging.send(
            db_session, tenant_id=tenant, message=SmsMessage(to=_RECIPIENT, body=_BODY), actor_id=None, purpose="p"
        )

    assert refused.value.details == {
        "reason": "messaging_not_connected", "channel": "sms", "link": "/administration/integrations"
    }
    assert recorder.requests == []
    assert MessagingChannel.SMS not in messaging.connected_channels(db_session, tenant_id=tenant)


# --- what never leaves the request -------------------------------------------------


def test_neither_the_credential_nor_the_number_nor_the_text_is_logged_or_stored(db_session, fake_provider, caplog):
    connection = _connection(db_session, "twilio")
    caplog.set_level(logging.DEBUG)
    fake_provider(Recorder(status=201, body=_TWILIO_ACCEPTED))
    _send(db_session, connection, SmsMessage(to=_RECIPIENT, body=_BODY))
    fake_provider(Recorder(status=400, body={"code": 21211, "message": f"{_RECIPIENT} {_BODY} {_SECRET}"}))
    with pytest.raises(ProviderRequestError):
        _send(db_session, connection, SmsMessage(to=_RECIPIENT, body=_BODY))

    for value in (_SECRET, _RECIPIENT, _BODY):
        assert value not in caplog.text
        for row in _rows(db_session, connection):
            assert value not in repr({c.key: getattr(row, c.key) for c in IntegrationCallLog.__table__.columns})


# --- usage, «Test connection», the tenant-only rule --------------------------------


def test_each_message_is_counted_on_the_usage_view_under_its_operation(db_session, fake_provider):
    fake_provider(Recorder(status=201, body=_TWILIO_ACCEPTED))
    connection = _connection(db_session, "twilio")
    for _ in range(2):
        _send(db_session, connection, SmsMessage(to=_RECIPIENT, body=_BODY))

    start, end = connection_service.usage_period(None, None)
    usage = connection_service.get_usage(db_session, connection=connection, period_from=start, period_to=end)

    assert [(c.counter, c.count) for c in usage.counters] == [("messages_create", 2)]


@pytest.mark.parametrize(
    ("code", "url", "body"),
    [
        ("twilio", f"https://api.twilio.com/2010-04-01/Accounts/{_ACCOUNT_SID}.json", {"sid": _ACCOUNT_SID}),
        (
            "meta_whatsapp",
            (
                f"https://graph.facebook.com/{META_GRAPH_VERSION}/{_PHONE_NUMBER_ID}"
                "?fields=display_phone_number%2Cverified_name"
            ),
            {"id": _PHONE_NUMBER_ID},
        ),
    ],
)
def test_test_connection_reads_the_account_and_sends_nothing(db_session, fake_provider, code, url, body):
    recorder = fake_provider(Recorder(body=body))
    connection = _connection(db_session, code)

    tested = gateway.test_connection(db_session, connection=connection, actor_id=uuid.uuid4())

    assert tested.status == ConnectionStatus.CONNECTED and tested.last_error is None
    assert [(r.method, str(r.url)) for r in recorder.requests] == [("GET", url)]


def test_test_connection_with_a_refused_token_reads_error_in_our_words(db_session, fake_provider):
    fake_provider(Recorder(status=401, body={"error": {"code": 190, "message": "Invalid OAuth access token."}}))
    connection = _connection(db_session, "meta_whatsapp")

    tested = gateway.test_connection(db_session, connection=connection, actor_id=uuid.uuid4())

    assert tested.status == ConnectionStatus.ERROR
    assert tested.last_error == "Meta refused the access token — check the token and test again."
    resilience.reset_circuit(connection.id)


@pytest.mark.parametrize("code", MESSAGING_PROVIDER_CODES)
def test_a_platform_wide_messaging_connection_is_refused(db_session, code):
    provider = _provider(db_session, code)
    config = _TWILIO_CONFIG if code == "twilio" else _META_CONFIG

    with pytest.raises(UnprocessableEntityError) as refused:
        connection_service.create_connection(
            db_session,
            tenant_id=None,
            data=ConnectionCreate(
                provider_id=provider.id, display_name="Nexotec SMS", environment=ConnectionEnvironment.PRODUCTION,
                scope=ConnectionScope.PLATFORM, config=config,
            ),
            actor_id=uuid.uuid4(),
        )

    assert refused.value.details["reason"] == "messaging_provider_tenant_only"
    created = connection_service.create_connection(
        db_session,
        tenant_id=uuid.uuid4(),
        data=ConnectionCreate(
            provider_id=provider.id, display_name="Garage SMS", environment=ConnectionEnvironment.PRODUCTION,
            config=config,
        ),
        actor_id=uuid.uuid4(),
    )
    assert created.scope == ConnectionScope.TENANT


def test_another_betriebs_messaging_connection_is_a_404(client, db_session):
    connection = _connection(db_session, "twilio")
    tenant = uuid.uuid4()
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant, group_id=dealer_group_of(str(tenant)), roles=frozenset(),
        is_dealer_manager=True,
    )

    response = client.get(
        f"/v1/integrations/connections/{connection.id}", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 404


def test_the_card_names_the_account_and_its_sender(client, db_session):
    tenant = uuid.uuid4()
    connection = _connection(db_session, "twilio", tenant_id=tenant)
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant, group_id=dealer_group_of(str(tenant)), roles=frozenset(),
        is_dealer_manager=True,
    )

    body = client.get(
        f"/v1/integrations/connections/{connection.id}", headers={"Authorization": f"Bearer {token}"}
    ).json()

    assert body["accountIdentifier"] == f"{_ACCOUNT_SID} · {_SERVICE_SID}"


# --- the migration's catalogue rows are the adapters' declaration ------------------


def test_the_migrations_provider_rows_match_the_adapters():
    (path,) = sorted(
        (Path(__file__).resolve().parents[2] / "alembic" / "versions" / "integration").glob("c7d2a9e4f1b8_*.py")
    )
    spec = importlib.util.spec_from_file_location("kan217_messaging_providers", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    rows = {code: (slots, keys) for code, _name, _auth, slots, keys, _docs in module._PROVIDERS}
    assert tuple(rows) == MESSAGING_PROVIDER_CODES
    assert rows["twilio"] == (
        [TwilioSmsAdapter.secret_slot.value],
        [
            messaging_providers.TWILIO_ACCOUNT_SID_KEY,
            messaging_providers.TWILIO_API_KEY_SID_KEY,
            messaging_providers.TWILIO_SENDER_KEY,
        ],
    )
    assert rows["meta_whatsapp"] == (
        [MetaWhatsAppAdapter.secret_slot.value], [messaging_providers.META_PHONE_NUMBER_ID_KEY]
    )
    assert {CHANNEL_PROVIDER[c] for c in MessagingChannel} == set(MESSAGING_PROVIDER_CODES)
