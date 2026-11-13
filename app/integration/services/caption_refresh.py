"""KAN-588 — «Captions aktualisieren»: platform staff acting in a Betrieb
refresh a provider's caption vocabulary by hand.

Anto ruled (2026-11-07) that no scheduled refresh is wanted — a background
job has no user to enrol at auto-i (ADR-111 as amended) — and that Nexotec
staff should have something when the vocabulary is out of sync. So the
refresh runs the same fetch «Test connection» runs (`GET /caption/code`, one
`caption_code` call-log row per language) through the Betrieb's own enabled
vehicle-data connection, **under the acting staff user's own token** (the
gateway's User Enrollment Grant names the actor). Each language is committed
as it lands, as on «Test connection»; a failure keeps what landed. Audited on
the connection (`captions_refreshed`, the languages fetched).

No daily job ever calls this (KAN-485 guard): only the staff route does.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import ConflictError, UnprocessableEntityError
from app.integration.errors import ProviderConfigurationError, ProviderGatewayError, ProviderNotEntitledError
from app.integration.models.connection import IntegrationConnection
from app.integration.models.provider import IntegrationProvider
from app.integration.services import captions as captions_service
from app.integration.services.gateway import _ADAPTER_FACTORIES, call_capability


def _vehicle_data_connection(db: Session, *, tenant_id: uuid.UUID) -> tuple[IntegrationConnection, str] | None:
    rows = db.execute(
        select(IntegrationConnection, IntegrationProvider.provider_code)
        .join(IntegrationProvider, IntegrationProvider.id == IntegrationConnection.provider_id)
        .where(
            IntegrationConnection.tenant_id == tenant_id,
            IntegrationConnection.enabled.is_(True),
            IntegrationProvider.category == "vehicle_data",
        )
        .order_by(IntegrationConnection.created_at)
    ).all()
    for connection, provider_code in rows:
        if provider_code in _ADAPTER_FACTORIES:
            return connection, provider_code
    return None


def _refusal(
    failure: ProviderGatewayError | ConflictError, fetched: list[str]
) -> ConflictError | UnprocessableEntityError:
    """The provider's failure in the words KAN-552's quote uses: the
    connection's set-up (the dealer's to fix) and the contract are said as
    such; anything else is «not available right now»."""

    if isinstance(failure, ConflictError):
        return failure  # provider_has_no_captions: the provider speaks no vocabulary
    if isinstance(failure, ProviderConfigurationError):
        return ConflictError(
            "The provider connection is not fully set up.",
            details={"reason": "provider_not_configured", "languagesFetched": fetched},
        )
    if isinstance(failure, ProviderNotEntitledError):
        return ConflictError(
            "Captions are not in this Betrieb's provider contract.",
            details={"reason": "not_entitled", "languagesFetched": fetched},
        )
    return UnprocessableEntityError(
        "The provider could not be asked for its captions.",
        details={"reason": "provider_unavailable", "languagesFetched": fetched},
    )


def refresh_captions(db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID) -> list[captions_service.CaptionFetch]:
    """Fetches the four languages for the Betrieb's vehicle-data provider.
    409 `no_provider_connection` without an enabled one; when the provider
    fails, 409 `provider_not_configured` / `not_entitled` /
    `provider_has_no_captions` or 422 `provider_unavailable` (the languages
    fetched before stay stored; every attempt is audited and names them)."""

    found = _vehicle_data_connection(db, tenant_id=tenant_id)
    if found is None:
        raise ConflictError(
            "This Betrieb has no enabled vehicle-data provider connection.", details={"reason": "no_provider_connection"}
        )
    connection, provider_code = found
    connection_id = connection.id
    fetched: list[str] = []
    failure: ProviderGatewayError | ConflictError | None = None
    try:
        with call_capability(
            db, connection=connection, capability="caption_code", actor_id=actor_id, purpose="captions_refresh"
        ) as adapter:
            fetch = getattr(adapter, "fetch_captions", None)
            if fetch is None:
                raise ConflictError(
                    "This provider has no caption vocabulary.", details={"reason": "provider_has_no_captions"}
                )
            for lang in captions_service.CAPTION_LANGUAGES:
                captions_service.store_captions(db, provider_code=provider_code, lang=lang, captions=fetch(lang))
                db.commit()  # each language kept as it lands
                fetched.append(lang)
    except (ProviderGatewayError, ConflictError) as exc:
        db.rollback()
        failure = exc
    record_audit_event(
        db,
        entity_type="integration_connection",
        entity_id=connection_id,
        tenant_id=tenant_id,
        action="captions_refreshed",
        actor_id=actor_id,
        after={"provider": provider_code, "languages": fetched, "complete": failure is None},
    )
    db.commit()
    if failure is not None:
        raise _refusal(failure, fetched) from failure
    return captions_service.list_caption_fetches(db)
