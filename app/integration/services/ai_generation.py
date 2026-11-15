"""`generate(dealership, code, variables)` (KAN-227, D-A-33, FR-A-38) — the
one place a prompt meets the Betrieb's AI provider.

Why here and not in `platform.public`, where the ticket first put it:
platform is the lowest layer and may not call integration, and generation is
a provider call — so it lives beside the gateway (ADR (KAN-227): AI prompts
and generation live in integration). Callers above
(Inventory's «Text vorschlagen») reach it through `integration.public`.

It resolves, in order: the Betrieb's AI connection (below), the prompt of the
code in the language chain (requested → the Betrieb's correspondence
language → DE; the Betrieb's override over the seed), renders the caller's
variables into it (`ai_prompt_registry.render`) and sends one completion
through `call_capability` — so the request is logged, its tokens are on its
call-log row (the usage view counts them) and a failing provider charges the
breaker like any other. The answer is a **suggestion**: returned to the
caller, stored nowhere, published by nothing (the user edits it).

**Which connection.** A Betrieb may connect more than one AI provider; the one
used is the enabled one most recently tested successfully (`connected`
first, then `last_verified_at`, then the newest) — deterministic, and named on
`/administration/ai` so the manager sees which one speaks. None → 409
`ai_not_connected`, which the screen turns into a link to the integrations.
"""

import uuid
from dataclasses import dataclass
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, UnprocessableEntityError
from app.core.i18n import SwissLanguage
from app.integration.adapters.ai_base import AI_CATEGORY, AI_TEXT_GENERATION_CAPABILITY, AiGateway
from app.integration.errors import ProviderConfigurationError, ProviderGatewayError
from app.integration.models.connection import ConnectionScope, ConnectionStatus, IntegrationConnection
from app.integration.models.provider import IntegrationProvider
from app.integration.services import ai_prompts
from app.integration.services.ai_prompt_registry import render, require_code
from app.integration.services.gateway import call_capability
from app.platform.public import get_dealership_default_correspondence_language


@dataclass(frozen=True)
class AiConnectionInUse:
    connection_id: uuid.UUID
    display_name: str
    provider_code: str
    provider_name: str
    status: ConnectionStatus


@dataclass(frozen=True)
class GeneratedText:
    text: str
    language: SwissLanguage
    # Whose prompt was used: the Betrieb's adaptation or Nexotec's default.
    prompt_adapted: bool
    provider_name: str


def _ai_connection(db: Session, tenant_id: uuid.UUID) -> tuple[IntegrationConnection, IntegrationProvider] | None:
    rows = db.execute(
        select(IntegrationConnection, IntegrationProvider)
        .join(IntegrationProvider, IntegrationProvider.id == IntegrationConnection.provider_id)
        .where(
            IntegrationConnection.tenant_id == tenant_id,
            IntegrationConnection.scope == ConnectionScope.TENANT,
            IntegrationConnection.enabled.is_(True),
            IntegrationProvider.category == AI_CATEGORY,
        )
    ).all()
    if not rows:
        return None

    def rank(pair: tuple[IntegrationConnection, IntegrationProvider]):
        connection = pair[0]
        verified = connection.last_verified_at.timestamp() if connection.last_verified_at else 0.0
        return (connection.status == ConnectionStatus.CONNECTED, verified, connection.created_at.timestamp())

    connection, provider = max((tuple(row) for row in rows), key=rank)  # type: ignore[arg-type]
    return connection, provider


def ai_connection_in_use(db: Session, *, tenant_id: uuid.UUID) -> AiConnectionInUse | None:
    found = _ai_connection(db, tenant_id)
    if found is None:
        return None
    connection, provider = found
    return AiConnectionInUse(
        connection_id=connection.id,
        display_name=connection.display_name,
        provider_code=provider.provider_code,
        provider_name=provider.display_name,
        status=connection.status,
    )


def generate(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    code: str,
    variables: dict[str, str | None],
    language: SwissLanguage | None,
    actor_id: uuid.UUID | None,
) -> GeneratedText:
    entry = require_code(code)
    found = _ai_connection(db, dealership_id)
    if found is None:
        raise ConflictError(
            "No AI provider is connected for this Betrieb.",
            details={"reason": "ai_not_connected", "link": "/administration/integrations"},
        )
    connection, provider = found
    default_language = get_dealership_default_correspondence_language(db, dealership_id)
    chain = ai_prompts.language_chain(language or default_language or ai_prompts.FALLBACK_LANGUAGE, default_language)
    prompt = ai_prompts.resolve_prompt(db, tenant_id=dealership_id, code=code, languages=chain)
    if prompt is None:
        raise ConflictError(f"No AI prompt {code} is installed.", details={"reason": "ai_prompt_missing", "code": code})
    system_prompt = render(entry, prompt.system_prompt, values=variables, language=prompt.language)
    user_prompt = render(entry, prompt.user_prompt, values=variables, language=prompt.language)
    # The caller's session is the caller's: nothing here commits it (ADR-047).
    # The call log and the audit row are written on the gateway's own session.
    prompt_language, adapted = prompt.language, prompt.tenant_id is not None

    try:
        with call_capability(
            db, connection=connection, capability=AI_TEXT_GENERATION_CAPABILITY, actor_id=actor_id, purpose=code
        ) as adapter:
            completion = cast(AiGateway, adapter).complete(
                system_prompt=system_prompt, user_prompt=user_prompt, max_output_tokens=entry.max_output_tokens
            )
    except ProviderConfigurationError as exc:
        raise ConflictError(
            str(exc), details={"reason": "ai_provider_not_configured", "link": "/administration/integrations"}
        ) from exc
    except ProviderGatewayError as exc:
        # `failure` is our adapter's own wording of what went wrong (errors.py:
        # an adapter never passes a provider's or a library's message on); the
        # screen shows the reason in the UI language, never this field.
        raise UnprocessableEntityError(
            "The AI provider did not deliver a suggestion.",
            details={"reason": "ai_provider_unavailable", "failure": str(exc)},
        ) from exc
    if not completion.text:
        raise UnprocessableEntityError(
            "The AI provider answered without a text.", details={"reason": "ai_provider_unavailable"}
        )
    return GeneratedText(
        text=completion.text, language=prompt_language, prompt_adapted=adapted, provider_name=provider.display_name
    )
