"""Google Business Profile on the status pages (KAN-776; D-A-38, FR-A-43;
ADR-195, ADR-196).

Every Business Profile request goes through Nexotec's own OAuth client, the
one platform-scope connection to `google_business_profile` (ADR-195). When
Google refuses that client or Nexotec's project, every Business Profile call
fails at once — today the manager's link flow (`gbp_link`); the push and drift
jobs (KAN-226 PR-3/PR-4) are not built yet — and the cause is Nexotec's to
fix, never the Betrieb's. Anto, 2026-11-20 (KAN-776, option C), rules who sees what:

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
  the worker's own (no worker job calls Business Profile yet);
- its latest **deciding** call-log row (`_deciding_rows`) carries a client-
  or project-level refusal. A row decides exactly as the adapter's
  `_raise_for_status` reads the same answer (KAN-782): it **raises** where
  the adapter raises `ProviderRejectedError` — `invalid_client` at any status
  (Google refused the OAuth client itself), one of ADR-196's
  `PROJECT_REFUSALS` on a 401 or 403 — and it **clears** where Google
  answered for the request itself: a success, or a 400 / 404 / 422 the
  adapter maps to `ProviderRequestError`. Everything else neither raises nor
  clears: a timeout, a 5xx, a quota answer, one binding's refused access or
  grant. The one rule of this read's own: a token-endpoint row decides only
  when it is `invalid_client` — a token granted says nothing about the
  project (Google reports a disabled API only on the API itself). Both
  refusals are charged to the breaker, so the refusal is Nexotec's setup,
  never one binding's. Staff make a clearing answer with «Erneut prüfen» once
  the cause is fixed (`gbp_client_check`; Anto, 2026-11-21, decision 1 A),
  and a manager's link flow makes one too. The mapping itself is the adapter's
  (`adapters/google_business_profile.py`) and is only read here.

Derived on every read, never stored; a read never writes (`app.core.status`).
"""

import dataclasses
import datetime as dt
import uuid

from sqlalchemy import ColumnElement, Select, and_, func, not_, or_, select
from sqlalchemy.orm import Session

from app.core.status import StatusItem, StatusState
from app.integration.adapters.google_business_profile import GRANT_REFUSALS, PROJECT_REFUSALS
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.google_business_profile import GbpBindingStatus, GbpLocationBinding
from app.integration.services import resilience
from app.integration.services.gbp_link import client_connection

SOURCE = "google_business_profile"
ITEM_KEY = "google_business_profile.client"
# Platform's notification for this item's degradation (Anto, 2026-11-21,
# decision 2 B): «Nexotec's connection is interrupted, we are on it — nothing
# to do on your side», never the generic «how to fix it». A plain code: the
# catalogue is platform's (`notification_catalogue.py`).
NOTIFICATION_EVENT = "administration.business_profile_interrupted"

# The refusal identifiers that name Nexotec's client or project rather than
# one binding's grant (ADR-196).
CLIENT_REFUSALS: frozenset[str] = PROJECT_REFUSALS | frozenset({"invalid_client"})
# The adapter's token-endpoint operations (`oauth2_token` redeems a code,
# `oauth2_token_refresh` refreshes a binding's grant).
TOKEN_OPERATIONS: frozenset[str] = frozenset({"oauth2_token", "oauth2_token_refresh"})
# The statuses the adapter maps to `ProviderRequestError`: Google answering
# for the request itself (`_raise_for_status`).
_REQUEST_STATUSES: tuple[int, ...] = (400, 404, 422)


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


def _code() -> ColumnElement:
    # NULL-safe: a row without an identifier compares as the empty string.
    return func.coalesce(IntegrationCallLog.provider_error, "")


def _raises() -> ColumnElement[bool]:
    """Where the adapter raises `ProviderRejectedError`: `invalid_client` at
    any status, a `PROJECT_REFUSALS` reason on a 401 or 403 (on a 429 it is
    transient; on a 400 / 404 / 422 an answer for the request)."""

    return or_(
        _code() == "invalid_client",
        and_(IntegrationCallLog.http_status.in_((401, 403)), _code().in_(PROJECT_REFUSALS)),
    )


def _clears() -> ColumnElement[bool]:
    """Where the adapter raises nothing, or `ProviderRequestError`: a success,
    or a 400 / 404 / 422 that is neither `invalid_client` nor a 400 naming one
    binding's refused grant (`GRANT_REFUSALS`, `ProviderAccountRefusedError`)."""

    return and_(
        _code() != "invalid_client",
        or_(
            IntegrationCallLog.http_status < 400,
            and_(
                IntegrationCallLog.http_status.in_(_REQUEST_STATUSES),
                not_(and_(IntegrationCallLog.http_status == 400, _code().in_(GRANT_REFUSALS))),
            ),
        ),
    )


def _deciding_rows() -> tuple:
    """The call-log rows that can say whether Google accepts Nexotec's client
    and project: an HTTP answer the adapter reads as the client's or the
    project's refusal (`_raises`) or as Google answering for the request
    (`_clears`) — exactly as `_raise_for_status` maps it (KAN-782). A
    timeout, a transport failure, a 5xx other than `invalid_client`, a quota
    answer on a 401 / 403 / 429, an `UNAUTHENTICATED`, a 403
    `PERMISSION_DENIED` or a refused grant proves nothing either way. A
    token-endpoint row decides only when it is `invalid_client`: a token
    granted says nothing about the project (Google reports a disabled API
    only on the API itself), and a refused grant is the binding's."""

    return (
        IntegrationCallLog.http_status.is_not(None),
        or_(_raises(), _clears()),
        or_(
            IntegrationCallLog.capability.not_in(TOKEN_OPERATIONS),
            _code() == "invalid_client",
        ),
    )


def _latest_call(connection_id: uuid.UUID) -> Select:
    """The connection's latest call-log row's time. Served newest first by
    `ix_integration_call_log_connection_id_created_at` (KAN-777)."""

    return (
        select(IntegrationCallLog.created_at)
        .where(IntegrationCallLog.connection_id == connection_id)
        .order_by(IntegrationCallLog.created_at.desc(), IntegrationCallLog.id.desc())
        .limit(1)
    )


def _latest_deciding_call(connection_id: uuid.UUID) -> Select:
    """The connection's latest deciding row (`_deciding_rows`), by the same
    index (KAN-777)."""

    return (
        select(IntegrationCallLog.provider_error, IntegrationCallLog.created_at, _raises().label("raises"))
        .where(IntegrationCallLog.connection_id == connection_id, *_deciding_rows())
        .order_by(IntegrationCallLog.created_at.desc(), IntegrationCallLog.id.desc())
        .limit(1)
    )


def _live_bindings():
    # «Live» is the code's own meaning (`uq_integration_gbp_binding_location_live`):
    # pending, active or error — a pending link needs the client to list its
    # choices. An abandoned pending link keeps its Betrieb's item until the
    # manager's next start replaces it.
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
    last_call_at = db.scalar(_latest_call(connection.id))
    deciding = db.execute(_latest_deciding_call(connection.id)).first()
    refusal = deciding.provider_error if deciding is not None and deciding.raises else None
    return GbpClientHealth(
        configured=True,
        connection_id=connection.id,
        breaker_open=resilience.circuit_is_open_now(connection.id),
        refusal=refusal,
        refused_at=deciding.created_at if deciding is not None and refusal is not None else None,
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
                notification_event=NOTIFICATION_EVENT,
            )
        ]
