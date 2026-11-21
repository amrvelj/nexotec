"""«Erneut prüfen» — staff ask Google again whether Nexotec's Business
Profile client works (KAN-776; Anto, 2026-11-21, decision 1 A; ADR-197).

The status item of every bound Betrieb clears only on a successful call on
Nexotec's client (`gbp_status`), and without this check nothing but a
manager's link flow makes one. Staff press the button on `/platform/status`
once they have fixed the cause at Google; this module makes **one real
request** through the client:

- with the oldest `active` binding's grant (the binding with a Google
  location): a fresh access token, then that location read with the smallest
  read mask (`name`). Nothing is stored; the request is metered and logged on
  the client connection like every Business Profile call, under the purpose
  `business_profile_client_check`;
- Google refusing that binding's grant is the binding's answer, not the
  client's: the binding goes to `error` (`gbp_link.mark_grant_refused`, as in
  the link flow) and the next active binding is tried, at most
  `MAX_BINDINGS_TRIED`;
- a client or project refusal is logged and the item stays blocked (the
  outcome `refused`); a transport failure or a timeout is `unavailable`; any
  answer Google gives for the location (a read, or a 404 for a location gone)
  proves the client and project are accepted: `answered`.

Refusals (409): no enabled client (`business_profile_not_configured`), no
active binding to ask with (`business_profile_no_active_binding`), the
breaker open (`business_profile_breaker_open`: try again in a minute), every
tried grant refused (`business_profile_no_active_binding`). Audited on the
client connection (`business_profile_client_checked`, the outcome), the staff
user as the actor. Commits.
"""

import dataclasses
import enum
import uuid
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import ConflictError
from app.integration.adapters.google_business_profile import GBP_CAPABILITY, GoogleBusinessProfileAdapter
from app.integration.errors import (
    ProviderAccountRefusedError,
    ProviderConfigurationError,
    ProviderGatewayError,
    ProviderRejectedError,
    ProviderRequestError,
)
from app.integration.models.connection import IntegrationConnection
from app.integration.models.google_business_profile import GbpBindingStatus, GbpLocationBinding
from app.integration.services import gbp_link, gbp_status
from app.integration.services.gateway import call_capability
from app.integration.services.resilience import CircuitOpenError, circuit_is_open_now

PURPOSE = "business_profile_client_check"
MAX_BINDINGS_TRIED = 3
_READ_MASK = ("name",)
_ENTITY_TYPE = "integration_connection"


class ClientCheckOutcome(str, enum.Enum):
    # Google answered for the location: client and project are accepted.
    ANSWERED = "answered"
    # Google refused Nexotec's client or project again.
    REFUSED = "refused"
    # Google could not be reached or did not answer in time.
    UNAVAILABLE = "unavailable"


@dataclasses.dataclass(frozen=True)
class ClientCheck:
    outcome: ClientCheckOutcome
    health: gbp_status.GbpClientHealth


def _refusal(reason: str, message: str) -> ConflictError:
    return ConflictError(message, details={"reason": reason})


def _no_binding() -> ConflictError:
    return _refusal(
        "business_profile_no_active_binding",
        "No connected location's grant is available to ask Google with.",
    )


def _ask(db: Session, connection: IntegrationConnection, binding: GbpLocationBinding, actor_id: uuid.UUID) -> None:
    with call_capability(
        db, connection=connection, capability=GBP_CAPABILITY, actor_id=actor_id, purpose=PURPOSE
    ) as raw:
        adapter = cast(GoogleBusinessProfileAdapter, raw)
        token = adapter.access_token(binding_id=binding.id)
        adapter.get_location(access_token=token, location_name=cast(str, binding.google_location_name), read_mask=_READ_MASK)


def check_client(db: Session, *, actor_id: uuid.UUID) -> ClientCheck:
    connection = gbp_link.client_connection(db)
    if connection is None:
        raise _refusal("business_profile_not_configured", "Nexotec has not set up Google Business Profile yet.")
    if circuit_is_open_now(connection.id):
        raise _refusal(
            "business_profile_breaker_open", "Too many failed calls just now; try again in a minute."
        )
    bindings = db.scalars(
        select(GbpLocationBinding)
        .where(
            GbpLocationBinding.status == GbpBindingStatus.ACTIVE,
            GbpLocationBinding.google_location_name.is_not(None),
        )
        .order_by(GbpLocationBinding.connected_at, GbpLocationBinding.id)
        .limit(MAX_BINDINGS_TRIED)
    ).all()
    if not bindings:
        raise _no_binding()
    connection_id = connection.id
    outcome: ClientCheckOutcome | None = None
    for binding in bindings:
        try:
            _ask(db, connection, binding, actor_id)
            outcome = ClientCheckOutcome.ANSWERED
        except ProviderAccountRefusedError:
            gbp_link.mark_grant_refused(db, binding)
            continue
        except ProviderConfigurationError as exc:
            raise _refusal(
                "business_profile_not_configured", "Nexotec's Google Business Profile client is incomplete."
            ) from exc
        except ProviderRejectedError:
            outcome = ClientCheckOutcome.REFUSED
        except ProviderRequestError:
            # A 400/404 for the location: Google accepted the client and the
            # project and answered for the location itself.
            outcome = ClientCheckOutcome.ANSWERED
        except CircuitOpenError as exc:
            raise _refusal(
                "business_profile_breaker_open", "Too many failed calls just now; try again in a minute."
            ) from exc
        except ProviderGatewayError:
            outcome = ClientCheckOutcome.UNAVAILABLE
        break
    if outcome is None:
        raise _no_binding()
    record_audit_event(
        db,
        entity_type=_ENTITY_TYPE,
        entity_id=connection_id,
        tenant_id=None,
        action="business_profile_client_checked",
        actor_id=actor_id,
        after={"outcome": outcome.value},
    )
    db.commit()
    return ClientCheck(outcome=outcome, health=gbp_status.client_health(db))
