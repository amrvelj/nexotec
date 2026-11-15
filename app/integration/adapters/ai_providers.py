"""The four AI provider adapters behind `AiGateway` (KAN-227, D-A-33):
OpenAI, Azure OpenAI, Mistral and Anthropic, over `httpx`.

One choke point, `_HttpAiAdapter._send`: one metered request per completion
under the provider's own operation name (`chat.completions`, `messages`), the
tokens the provider reports written on that request's call-log row, and every
failure translated into the `app.integration.errors` vocabulary with words of
ours — never a library's message, never the key:

- 401 → `ProviderRejectedError` (the key is refused);
- 403 → `ProviderNotEntitledError` (the account may not use this model);
- 400 / 404 / 422 → `ProviderRequestError` (a model or deployment that does
  not exist is the usual cause; not charged to the breaker);
- 429 → `ProviderRejectedError` (rate limit or the account's quota — the
  dealer's provider account, the dealer's bill);
- 5xx, a transport failure → `ProviderTransportError`; a timeout →
  `ProviderTimeoutError`.

**No retry.** A completion is billed by the provider; a second attempt after a
timeout may bill twice for one suggestion. The user presses the button again.

The wire formats (as the providers document them publicly; verified against
fake transports only — no real account was called in this change):

- OpenAI and Mistral: `POST {base}/v1/chat/completions`, `Authorization:
  Bearer <key>`, `messages` with a `system` and a `user` entry; the answer's
  `choices[0].message.content`, `usage.prompt_tokens` / `completion_tokens`.
  OpenAI's output limit is `max_completion_tokens` (its reasoning models refuse
  `max_tokens`); Mistral's is `max_tokens`.
- Azure OpenAI: the OpenAI body without `model`, `POST
  {endpoint}/openai/deployments/{deployment}/chat/completions?api-version=…`,
  `api-key: <key>`. The endpoint is the dealer's resource URL, so it is
  checked before anything is sent: `https`, a host under `openai.azure.com`
  or `cognitiveservices.azure.com`, no path, port, user or query — the server
  never calls a URL a dealer typed (no SSRF through the configuration).
- Anthropic: `POST https://api.anthropic.com/v1/messages`, `x-api-key`,
  `anthropic-version: 2023-06-01`, `system` beside one `user` message,
  `max_tokens`; the answer's `content[]` text blocks, `usage.input_tokens` /
  `output_tokens`.
"""

import uuid
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.integration.adapters.ai_base import AiCompletion, AiGateway
from app.integration.errors import (
    ProviderConfigurationError,
    ProviderNotEntitledError,
    ProviderRejectedError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from app.integration.models.connection import IntegrationConnection
from app.integration.models.secret_ref import SecretSlot
from app.integration.services import secrets_backend
from app.integration.services.metering import provider_request

OPENAI = "openai"
AZURE_OPENAI = "azure_openai"
MISTRAL = "mistral"
ANTHROPIC = "anthropic"
AI_PROVIDER_CODES = (OPENAI, AZURE_OPENAI, MISTRAL, ANTHROPIC)

MODEL_CONFIG_KEY = "model"
AZURE_ENDPOINT_CONFIG_KEY = "endpoint"
AZURE_DEPLOYMENT_CONFIG_KEY = "deployment"
AZURE_API_VERSION_CONFIG_KEY = "apiVersion"

_OPENAI_BASE = "https://api.openai.com"
_MISTRAL_BASE = "https://api.mistral.ai"
_ANTHROPIC_BASE = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"
_AZURE_HOST_SUFFIXES = (".openai.azure.com", ".cognitiveservices.azure.com")

# A listing text is written in seconds; a minute is generous, ten seconds to connect.
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
# «Test connection»: the smallest billed completion that proves key and model.
_VERIFY_SYSTEM = "Answer with the single word OK."
_VERIFY_USER = "OK?"
_VERIFY_MAX_TOKENS = 16


def http_transport() -> httpx.BaseTransport | None:
    """The transport every adapter's client uses — None, httpx's own, in the
    application; a test replaces this function with a `MockTransport`."""

    return None


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.TimeoutException, ProviderTimeoutError))


class _HttpAiAdapter:
    operation = "chat.completions"
    provider_label = "The AI provider"

    def __init__(
        self, db: Session, connection: IntegrationConnection, actor_id: uuid.UUID | None, purpose: str
    ) -> None:
        self._db = db
        self._connection = connection
        self._actor_id = actor_id
        self._purpose = purpose

    # -- configuration -----------------------------------------------------

    def _config(self, key: str) -> str:
        value = (self._connection.config or {}).get(key)
        if not isinstance(value, str) or not value.strip():
            raise ProviderConfigurationError(
                f"This connection has no '{key}' set — enter it in the connection's settings and test again."
            )
        return value.strip()

    def _api_key(self) -> str:
        try:
            value = secrets_backend.resolve_secret(connection_id=self._connection.id, slot=SecretSlot.API_KEY.value)
        except Exception as exc:
            # Class name only: the secrets manager's message may name its path.
            raise ProviderConfigurationError(
                f"The '{SecretSlot.API_KEY.value}' credential of this connection could not be read "
                f"({type(exc).__name__}) — set it and test again."
            ) from exc
        record_audit_event(
            self._db,
            entity_type="integration_secret_ref",
            entity_id=self._connection.id,
            tenant_id=self._connection.tenant_id,
            action="secret_resolved",
            actor_id=self._actor_id,
            reason=f"slot={SecretSlot.API_KEY.value} purpose={self._purpose}",
        )
        return value

    # -- the one choke point -----------------------------------------------

    def _request(self, *, system_prompt: str, user_prompt: str, max_output_tokens: int) -> tuple[str, dict[str, str], dict[str, Any]]:
        raise NotImplementedError

    def _parse(self, body: dict[str, Any]) -> AiCompletion:
        raise NotImplementedError

    def _send(self, *, system_prompt: str, user_prompt: str, max_output_tokens: int) -> AiCompletion:
        url, headers, payload = self._request(
            system_prompt=system_prompt, user_prompt=user_prompt, max_output_tokens=max_output_tokens
        )
        with provider_request(self.operation, is_timeout=_is_timeout) as request:
            response = request.attempt(lambda: self._post(url, headers, payload))
            completion = self._parse(self._json(response))
            if completion.tokens_in is not None and completion.tokens_out is not None:
                request.report_tokens(completion.tokens_in, completion.tokens_out)
            return completion

    def _post(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> httpx.Response:
        try:
            with httpx.Client(timeout=_TIMEOUT, transport=http_transport()) as client:
                response = client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"{self.provider_label} did not answer in time ({type(exc).__name__}).") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransportError(f"{self.provider_label} could not be reached ({type(exc).__name__}).") from exc
        self._raise_for_status(response)
        return response

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        if status == 401:
            raise ProviderRejectedError(f"{self.provider_label} refused the API key (HTTP 401) — check the key.")
        if status == 403:
            raise ProviderNotEntitledError(
                self.operation, f"{self.provider_label}: the account may not use this model (HTTP 403)."
            )
        if status in (400, 404, 422):
            raise ProviderRequestError(
                f"{self.provider_label} refused the request (HTTP {status}) — check the model or deployment name."
            )
        if status == 429:
            raise ProviderRejectedError(
                f"{self.provider_label} refused the request (HTTP 429): the account's rate limit or quota is reached."
            )
        raise ProviderTransportError(f"{self.provider_label} failed to answer (HTTP {status}).")

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderTransportError(f"{self.provider_label} sent an answer that is not JSON.") from exc
        if not isinstance(body, dict):
            raise ProviderTransportError(f"{self.provider_label} sent an answer of an unexpected shape.")
        return body

    # -- AiGateway ---------------------------------------------------------

    def complete(self, *, system_prompt: str, user_prompt: str, max_output_tokens: int) -> AiCompletion:
        return self._send(system_prompt=system_prompt, user_prompt=user_prompt, max_output_tokens=max_output_tokens)

    def verify(self) -> AiCompletion:
        return self._send(system_prompt=_VERIFY_SYSTEM, user_prompt=_VERIFY_USER, max_output_tokens=_VERIFY_MAX_TOKENS)


def _usage(body: dict[str, Any]) -> dict[str, Any]:
    usage = body.get("usage")
    return usage if isinstance(usage, dict) else {}


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class _ChatCompletionsAdapter(_HttpAiAdapter):
    """OpenAI's chat-completions shape, which Azure OpenAI and Mistral share."""

    max_tokens_field = "max_completion_tokens"

    def _messages(self, system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

    def _parse(self, body: dict[str, Any]) -> AiCompletion:
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderTransportError(f"{self.provider_label} sent an answer without a text.") from exc
        if not isinstance(text, str):
            raise ProviderTransportError(f"{self.provider_label} sent an answer without a text.")
        usage = _usage(body)
        return AiCompletion(
            text=text.strip(),
            tokens_in=_int_or_none(usage.get("prompt_tokens")),
            tokens_out=_int_or_none(usage.get("completion_tokens")),
        )


class OpenAiAdapter(_ChatCompletionsAdapter):
    provider_label = "OpenAI"
    base_url = _OPENAI_BASE

    def _request(self, *, system_prompt, user_prompt, max_output_tokens):
        model = self._config(MODEL_CONFIG_KEY)
        headers = {"Authorization": f"Bearer {self._api_key()}"}
        payload = {
            "model": model,
            "messages": self._messages(system_prompt, user_prompt),
            self.max_tokens_field: max_output_tokens,
        }
        return f"{self.base_url}/v1/chat/completions", headers, payload


class MistralAdapter(OpenAiAdapter):
    provider_label = "Mistral"
    base_url = _MISTRAL_BASE
    max_tokens_field = "max_tokens"


def azure_endpoint(value: str) -> str:
    """The dealer's Azure OpenAI resource URL, refused unless it is one —
    `https://<resource>.openai.azure.com` (or `.cognitiveservices.azure.com`)
    and nothing else. Returns it without a trailing slash."""

    try:
        # urlsplit and .port raise ValueError on a malformed address (a bad
        # port, an unclosed IPv6 bracket): that is the dealer's configuration,
        # refused uncounted like any other, never a 500 (KAN-227 review).
        parts = urlsplit(value.strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        port, host, parts = -1, "", None
    if (
        parts is None
        or port is not None
        or parts.scheme != "https"
        or not host.endswith(_AZURE_HOST_SUFFIXES)
        or host in {suffix.lstrip(".") for suffix in _AZURE_HOST_SUFFIXES}
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise ProviderConfigurationError(
            "The endpoint must be the Azure OpenAI resource address, "
            "https://<resource>.openai.azure.com — nothing after it."
        )
    return f"https://{host}"


class AzureOpenAiAdapter(_ChatCompletionsAdapter):
    provider_label = "Azure OpenAI"

    def _request(self, *, system_prompt, user_prompt, max_output_tokens):
        endpoint = azure_endpoint(self._config(AZURE_ENDPOINT_CONFIG_KEY))
        deployment = self._config(AZURE_DEPLOYMENT_CONFIG_KEY)
        api_version = self._config(AZURE_API_VERSION_CONFIG_KEY)
        if not deployment.replace("-", "").replace("_", "").replace(".", "").isalnum():
            raise ProviderConfigurationError("The deployment name may hold letters, digits, '-', '_' and '.' only.")
        url = str(
            httpx.URL(f"{endpoint}/openai/deployments/{deployment}/chat/completions", params={"api-version": api_version})
        )
        headers = {"api-key": self._api_key()}
        payload = {"messages": self._messages(system_prompt, user_prompt), self.max_tokens_field: max_output_tokens}
        return url, headers, payload


class AnthropicAdapter(_HttpAiAdapter):
    provider_label = "Anthropic"
    operation = "messages"

    def _request(self, *, system_prompt, user_prompt, max_output_tokens):
        model = self._config(MODEL_CONFIG_KEY)
        headers = {"x-api-key": self._api_key(), "anthropic-version": _ANTHROPIC_VERSION}
        payload = {
            "model": model,
            "max_tokens": max_output_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        return f"{_ANTHROPIC_BASE}/v1/messages", headers, payload

    def _parse(self, body: dict[str, Any]) -> AiCompletion:
        blocks = body.get("content")
        if not isinstance(blocks, list):
            raise ProviderTransportError("Anthropic sent an answer without a text.")
        texts = [
            b["text"] for b in blocks if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        ]
        if not texts:
            raise ProviderTransportError("Anthropic sent an answer without a text.")
        usage = _usage(body)
        return AiCompletion(
            text="".join(texts).strip(),
            tokens_in=_int_or_none(usage.get("input_tokens")),
            tokens_out=_int_or_none(usage.get("output_tokens")),
        )


_ADAPTERS: dict[str, type[_HttpAiAdapter]] = {
    OPENAI: OpenAiAdapter,
    AZURE_OPENAI: AzureOpenAiAdapter,
    MISTRAL: MistralAdapter,
    ANTHROPIC: AnthropicAdapter,
}


def build_adapter(
    provider_code: str, db: Session, connection: IntegrationConnection, actor_id: uuid.UUID | None, purpose: str
) -> AiGateway:
    """Builds no network connection and resolves no secret: both happen on
    the first request, inside the caller's `call_capability` block."""

    return _ADAPTERS[provider_code](db, connection, actor_id, purpose)


def _register() -> None:
    # Imported lazily, as the other adapters do: nothing in adapters/ imports
    # gateway.py at module scope. The registry's factory type names
    # ProviderAdapter; it is polymorphic at runtime (AutoScout24 casts the
    # same way), and `services/ai_generation.py` casts back to AiGateway.
    from app.integration.adapters.base import ProviderAdapter
    from app.integration.services.gateway import register_adapter_factory

    for code in AI_PROVIDER_CODES:
        factory = cast(
            Callable[[Session, IntegrationConnection, uuid.UUID | None, str], ProviderAdapter],
            lambda db, connection, actor_id, purpose, code=code: build_adapter(code, db, connection, actor_id, purpose),
        )
        register_adapter_factory(code, factory)


_register()
