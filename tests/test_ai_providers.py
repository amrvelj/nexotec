"""KAN-227: the four AI provider adapters behind `AiGateway` — each provider's
wire format (as documented publicly), the tokens it reports, the failure
vocabulary, the Azure endpoint guard, and «Test connection» on an AI
connection. Fake transports only: no real account is called."""

import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.integration.adapters import ai_providers
from app.integration.adapters.ai_providers import azure_endpoint, build_adapter
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
from app.integration.services import gateway, resilience, secrets_backend

_KEY = "sk-test-not-a-real-key"


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

    @property
    def payload(self) -> dict:
        return json.loads(self.requests[-1].content)


@pytest.fixture
def fake_provider(monkeypatch):
    def install(recorder: Recorder) -> Recorder:
        monkeypatch.setattr(ai_providers, "http_transport", lambda: httpx.MockTransport(recorder))
        return recorder

    monkeypatch.setattr(secrets_backend, "resolve_secret", lambda *, connection_id, slot: _KEY)
    return install


def _openai_answer(text: str = "Ein schöner Kombi.", prompt_tokens: int = 120, completion_tokens: int = 45) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": f"  {text}\n"}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def _connection(db_session, provider_code: str, config: dict) -> IntegrationConnection:
    provider = IntegrationProvider(
        provider_code=provider_code, category="ai", display_name=provider_code, auth_type="api_key",
        required_secret_slots=["api_key"], required_config_keys=list(config), capability_codes=["ai_text_generation"],
        supports_sandbox=False,
    )
    db_session.add(provider)
    db_session.flush()
    connection = IntegrationConnection(
        provider_id=provider.id, scope=ConnectionScope.TENANT, tenant_id=uuid.uuid4(), display_name=provider_code,
        environment=ConnectionEnvironment.PRODUCTION, config=config, enabled=True,
    )
    db_session.add(connection)
    db_session.commit()
    return connection


def _complete(db_session, connection, **kwargs):
    adapter = build_adapter(
        db_session.get(IntegrationProvider, connection.provider_id).provider_code, db_session, connection, None, "test"
    )
    return adapter.complete(system_prompt="SYS", user_prompt="USER", max_output_tokens=kwargs.get("max_output_tokens", 300))


def test_openai_sends_chat_completions_with_the_dealers_model_and_reads_tokens(db_session, fake_provider):
    recorder = fake_provider(Recorder(body=_openai_answer()))
    connection = _connection(db_session, "openai", {"model": "dealer-chosen-model"})

    completion = _complete(db_session, connection)

    request = recorder.requests[0]
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {_KEY}"
    assert recorder.payload == {
        "model": "dealer-chosen-model",
        "messages": [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}],
        "max_completion_tokens": 300,
    }
    assert (completion.text, completion.tokens_in, completion.tokens_out) == ("Ein schöner Kombi.", 120, 45)


def test_mistral_uses_its_own_base_and_max_tokens(db_session, fake_provider):
    recorder = fake_provider(Recorder(body=_openai_answer()))
    connection = _connection(db_session, "mistral", {"model": "dealer-model"})

    _complete(db_session, connection)

    assert str(recorder.requests[0].url) == "https://api.mistral.ai/v1/chat/completions"
    assert recorder.payload["max_tokens"] == 300 and "max_completion_tokens" not in recorder.payload


def test_azure_calls_the_dealers_deployment_with_the_api_key_header(db_session, fake_provider):
    recorder = fake_provider(Recorder(body=_openai_answer()))
    connection = _connection(
        db_session, "azure_openai",
        {"endpoint": "https://garage-muster.openai.azure.com/", "deployment": "listing-texts", "apiVersion": "2024-10-21"},
    )

    _complete(db_session, connection)

    request = recorder.requests[0]
    assert str(request.url) == (
        "https://garage-muster.openai.azure.com/openai/deployments/listing-texts/chat/completions?api-version=2024-10-21"
    )
    assert request.headers["api-key"] == _KEY and "authorization" not in request.headers
    assert "model" not in recorder.payload


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://garage.openai.azure.com",  # not https
        "https://evil.example.com",  # not Azure
        "https://openai.azure.com",  # no resource
        "https://garage.openai.azure.com:8443",  # a port
        "https://garage.openai.azure.com/elsewhere",  # a path
        "https://user:pw@garage.openai.azure.com",  # credentials
        "https://garage.openai.azure.com.evil.example.com",  # a suffix trick
        "https://169.254.169.254",  # the metadata service
    ],
)
def test_azure_refuses_any_endpoint_but_an_azure_openai_resource(endpoint):
    with pytest.raises(ProviderConfigurationError):
        azure_endpoint(endpoint)


def test_azure_refused_endpoint_sends_nothing(db_session, fake_provider):
    recorder = fake_provider(Recorder(body=_openai_answer()))
    connection = _connection(
        db_session, "azure_openai", {"endpoint": "https://intranet.local", "deployment": "d", "apiVersion": "v"}
    )
    with pytest.raises(ProviderConfigurationError):
        _complete(db_session, connection)
    assert recorder.requests == []


def test_anthropic_sends_messages_with_system_beside_the_user_message(db_session, fake_provider):
    recorder = fake_provider(
        Recorder(
            body={
                "content": [{"type": "text", "text": "Gepflegter "}, {"type": "text", "text": "Kombi."}],
                "usage": {"input_tokens": 99, "output_tokens": 12},
            }
        )
    )
    connection = _connection(db_session, "anthropic", {"model": "dealer-model"})

    completion = _complete(db_session, connection)

    request = recorder.requests[0]
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == _KEY and request.headers["anthropic-version"] == "2023-06-01"
    assert recorder.payload == {
        "model": "dealer-model",
        "max_tokens": 300,
        "system": "SYS",
        "messages": [{"role": "user", "content": "USER"}],
    }
    assert (completion.text, completion.tokens_in, completion.tokens_out) == ("Gepflegter Kombi.", 99, 12)


def test_a_connection_without_a_model_is_the_dealers_to_fix(db_session, fake_provider):
    recorder = fake_provider(Recorder(body=_openai_answer()))
    connection = _connection(db_session, "openai", {})
    with pytest.raises(ProviderConfigurationError, match="model"):
        _complete(db_session, connection)
    assert recorder.requests == []


def test_an_unreadable_key_is_a_configuration_error_naming_no_path(db_session, monkeypatch):
    def boom(*, connection_id, slot):
        raise RuntimeError("secret path /tenants/abc/api_key not found")

    monkeypatch.setattr(secrets_backend, "resolve_secret", boom)
    connection = _connection(db_session, "openai", {"model": "m"})
    with pytest.raises(ProviderConfigurationError) as caught:
        _complete(db_session, connection)
    assert "/tenants" not in str(caught.value) and "RuntimeError" in str(caught.value)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, ProviderRejectedError),
        (403, ProviderNotEntitledError),
        (400, ProviderRequestError),
        (404, ProviderRequestError),
        (422, ProviderRequestError),
        (429, ProviderRejectedError),
        (500, ProviderTransportError),
        (529, ProviderTransportError),
    ],
)
def test_http_failures_are_our_vocabulary_without_the_providers_words(db_session, fake_provider, status, error):
    fake_provider(Recorder(status=status, body={"error": {"message": "secret internal detail sk-leak"}}))
    connection = _connection(db_session, "openai", {"model": "m"})
    with pytest.raises(error) as caught:
        _complete(db_session, connection)
    assert "sk-leak" not in str(caught.value) and "secret internal detail" not in str(caught.value)


def test_a_timeout_is_a_timeout_and_a_transport_failure_a_transport_error(db_session, fake_provider):
    connection = _connection(db_session, "openai", {"model": "m"})
    fake_provider(Recorder(raise_exc=httpx.ReadTimeout("slow")))
    with pytest.raises(ProviderTimeoutError):
        _complete(db_session, connection)
    fake_provider(Recorder(raise_exc=httpx.ConnectError("refused")))
    with pytest.raises(ProviderTransportError):
        _complete(db_session, connection)


@pytest.mark.parametrize("body", [{}, {"choices": []}, {"choices": [{"message": {"content": None}}]}, ["x"]])
def test_an_answer_without_a_text_is_refused(db_session, fake_provider, body):
    fake_provider(Recorder(body=body))
    connection = _connection(db_session, "openai", {"model": "m"})
    with pytest.raises(ProviderTransportError):
        _complete(db_session, connection)


def test_through_the_gateway_one_row_per_request_with_its_tokens_and_no_retry(db_session, fake_provider):
    recorder = fake_provider(Recorder(body=_openai_answer(prompt_tokens=200, completion_tokens=80)))
    connection = _connection(db_session, "openai", {"model": "m"})

    with gateway.call_capability(db_session, connection=connection, capability="ai_text_generation") as adapter:
        adapter.complete(system_prompt="S", user_prompt="U", max_output_tokens=10)  # type: ignore[attr-defined]

    rows = db_session.scalars(select(IntegrationCallLog).where(IntegrationCallLog.connection_id == connection.id)).all()
    assert [(r.capability, r.status, r.tokens_in, r.tokens_out) for r in rows] == [
        ("chat.completions", CallStatus.SUCCESS, 200, 80)
    ]

    # A timeout is never retried (a completion is billed): one request, one TIMEOUT row.
    recorder = fake_provider(Recorder(raise_exc=httpx.ReadTimeout("slow")))
    resilience.record_success(connection.id)
    with (
        pytest.raises(ProviderTimeoutError),
        gateway.call_capability(db_session, connection=connection, capability="ai_text_generation") as adapter,
    ):
        adapter.complete(system_prompt="S", user_prompt="U", max_output_tokens=10)  # type: ignore[attr-defined]
    assert len(recorder.requests) == 1
    statuses = db_session.scalars(
        select(IntegrationCallLog.status).where(IntegrationCallLog.connection_id == connection.id)
    ).all()
    assert sorted(s.value for s in statuses) == ["success", "timeout"]
    resilience.record_success(connection.id)


def test_test_connection_on_an_ai_provider_is_one_small_completion(db_session, fake_provider):
    recorder = fake_provider(Recorder(body=_openai_answer(text="OK", prompt_tokens=10, completion_tokens=1)))
    connection = _connection(db_session, "openai", {"model": "m"})

    tested = gateway.test_connection(db_session, connection=connection, actor_id=uuid.uuid4())

    assert tested.status == ConnectionStatus.CONNECTED and tested.last_error is None
    assert len(recorder.requests) == 1  # no captions, no licence probes
    assert recorder.payload["max_completion_tokens"] <= 16
    rows = db_session.scalars(select(IntegrationCallLog).where(IntegrationCallLog.connection_id == connection.id)).all()
    assert [(r.capability, r.tokens_in, r.tokens_out) for r in rows] == [("chat.completions", 10, 1)]


def test_test_connection_with_a_refused_key_reads_error_in_our_words(db_session, fake_provider):
    fake_provider(Recorder(status=401, body={"error": {"message": "Incorrect API key provided: sk-...abcd"}}))
    connection = _connection(db_session, "anthropic", {"model": "m"})

    tested = gateway.test_connection(db_session, connection=connection, actor_id=uuid.uuid4())

    assert tested.status == ConnectionStatus.ERROR
    assert tested.last_error == "Anthropic refused the API key (HTTP 401) — check the key."
    resilience.record_success(connection.id)
