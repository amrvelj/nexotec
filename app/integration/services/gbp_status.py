"""Google Business Profile on the status pages (KAN-776; D-A-38, FR-A-43;
ADR-195, ADR-196).

Every Business Profile request goes through Nexotec's own OAuth client, the
one platform-scope connection to `google_business_profile` (ADR-195). When
Google refuses that client or Nexotec's project, every bound location's push
and drift job stops at once, and the cause is Nexotec's to fix, never the
Betrieb's. Anto, 2026-11-20 (KAN-776, option C), rules who sees what:

- **Staff** see the cause on the platform status view: whether the client is
  set up, whether its breaker is open, the latest call's refusal identifier
  (`SERVICE_DISABLED`, `invalid_client`, …) and when it came, and how many
  Betriebe hold a live binding (`client_health`, served by
  `GET /v1/integrations/business-profile/client-status`, staff only).
- **Each Betrieb with a live binding** sees one item on its own status page,
  «Google Business Profile: Nexotec's connection is interrupted, we are on
  it» (`GbpStatusProvider`). It carries no link and no parameter: it is never
  presented as the Betrieb's to fix and never names Google's reason or
  another Betrieb. A Betrieb without a live binding has no item.

The client counts as interrupted when any of these holds:

- no enabled platform-scope client connection exists (a live binding cannot
  reach Google without one);
- its circuit breaker is open in this process (`resilience`): the gateway
  charged it five times in a row. The breaker lives in memory per process,
  so the API's status page sees the API's calls and the worker's daily job
  the worker's own, where the push and drift jobs run;
- its latest call-log row carries a client- or project-level refusal: one of
  ADR-196's `PROJECT_REFUSALS`, or `invalid_client` (Google refused the OAuth
  client itself). The adapter maps both to `ProviderRejectedError`, charged to
  the breaker, so the refusal is Nexotec's setup, never one binding's. A
  later successful call clears it. The mapping itself is the adapter's
  (`adapters/google_business_profile.py`) and is only read here.

Derived on every read, never stored; a read never writes (`app.core.status`).
"""

import dataclasses
import datetime as dt
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.status import StatusItem, StatusState
from app.integration.adapters.google_business_profile import PROJECT_REFUSALS
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.google_business_profile import GbpBindingStatus, GbpLocationBinding
from app.integration.services import resilience
from app.integration.services.gbp_link import client_connection

SOURCE = "google_business_profile"
ITEM_KEY = "google_business_profile.client"

# The refusal identifiers that name Nexotec's client or project rather than
# one binding's grant (ADR-196).
CLIENT_REFUSALS: frozenset[str] = PROJECT_REFUSALS | frozenset({"invalid_client"})


@dataclasses.dataclass(frozen=True)
class GbpClientHealth:
    # An enabled platform-scope client connection exists.
    configured: bool
    connection_id: uuid.UUID | None
    breaker_open: bool
    # The latest call's refusal identifier when it is a client or project
    # refusal, else None.
    refusal: str | None
    refused_at: dt.datetime | None
    last_call_at: dt.datetime | None
    # Betriebe holding at least one live (not disconnected) binding.
    bound_dealerships: int

    @property
    def interrupted(self) -> bool:
        return not self.configured or self.breaker_open or self.refusal is not None


def _live_bindings():
    return GbpLocationBinding.status != GbpBindingStatus.DISCONNECTED


def client_health(db: Session, *, count_bindings: bool = True) -> GbpClientHealth:
    """What staff see: the client's state and its cause. A read.
    `count_bindings=False` skips the cross-Betrieb count (`bound_dealerships`
    is then 0), for the Betrieb's own item, which never shows it."""

    bound = (
        db.scalar(select(func.count(func.distinct(GbpLocationBinding.tenant_id))).where(_live_bindings())) or 0
        if count_bindings
        else 0
    )
    connection = client_connection(db)
    if connection is None:
        return GbpClientHealth(
            configured=False,
            connection_id=None,
            breaker_open=False,
            refusal=None,
            refused_at=None,
            last_call_at=None,
            bound_dealerships=bound,
        )
    latest = db.execute(
        select(IntegrationCallLog.provider_error, IntegrationCallLog.created_at)
        .where(IntegrationCallLog.connection_id == connection.id)
        .order_by(IntegrationCallLog.created_at.desc(), IntegrationCallLog.id.desc())
        .limit(1)
    ).first()
    last_call_at = latest.created_at if latest is not None else None
    refusal = latest.provider_error if latest is not None and latest.provider_error in CLIENT_REFUSALS else None
    return GbpClientHealth(
        configured=True,
        connection_id=connection.id,
        breaker_open=resilience.circuit_is_open_now(connection.id),
        refusal=refusal,
        refused_at=last_call_at if refusal is not None else None,
        last_call_at=last_call_at,
        bound_dealerships=bound,
    )


class GbpStatusProvider:
    """The Betrieb's one Business Profile item (KAN-776, option C):
    blocked while Nexotec's client is interrupted, ok otherwise, absent for a
    Betrieb with no live binding. No link, no params: nothing on the dealer
    side fixes it, and the cause is staff's to read."""

    source = SOURCE

    def items(self, db: Session, *, dealership_id: uuid.UUID, now: dt.datetime) -> list[StatusItem]:
        bound = db.scalar(
            select(GbpLocationBinding.id)
            .where(GbpLocationBinding.tenant_id == dealership_id, _live_bindings())
            .limit(1)
        )
        if bound is None:
            return []
        interrupted = client_health(db, count_bindings=False).interrupted
        return [
            StatusItem(
                key=ITEM_KEY,
                source=SOURCE,
                state=StatusState.BLOCKED if interrupted else StatusState.OK,
                message_key=f"statusPage.items.googleBusinessProfile.{'interrupted' if interrupted else 'ok'}",
                link=None,
            )
        ]
