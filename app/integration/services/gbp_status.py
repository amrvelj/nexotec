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
- its latest **deciding** call-log row (`_deciding_rows`: an HTTP answer below
  500, not a quota answer, a 401 / 403 / 429 only when its identifier is a
  client or project refusal (KAN-780), and not a token-endpoint row unless
  that row is `invalid_client`) carries a client- or project-level refusal: one of
  ADR-196's `PROJECT_REFUSALS`, or `invalid_client` (Google refused the OAuth
  client itself). A timeout, a 5xx or a refreshed token neither raises nor
  clears it. The adapter maps both to `ProviderRejectedError`, charged to
  the breaker, so the refusal is Nexotec's setup, never one binding's. Only a
  later deciding answer that is not such a refusal clears it: staff make one with
  «Erneut prüfen» once the cause is fixed (`gbp_client_check`; Anto,
  2026-11-21, decision 1 A), and a manager's link flow makes one too. The mapping itself is the adapter's
  (`adapters/google_business_profile.py`) and is only read here.

Derived on every read, never stored; a read never writes (`app.core.status`).
"""

import dataclasses
import datetime as dt
import uuid

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.status import StatusItem, StatusState
from app.core.text_collation import TextSortKey
from app.integration.adapters.google_business_profile import PROJECT_REFUSALS, QUOTA_REASONS
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.google_business_profile import (
    GbpBindingStatus,
    GbpDriftCheck,
    GbpDriftOutcome,
    GbpLocationBinding,
)
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
# The statuses Google also answers for one token or one quota (KAN-780): such a
# row decides only when its identifier names the client or the project.
_UNJUDGED_STATUSES: tuple[int, ...] = (401, 403, 429)


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


def _deciding_rows() -> tuple:
    """The call-log rows that can say whether Google accepts Nexotec's client
    and project: an HTTP answer below 500 that is not a quota answer (a quota
    passes by itself) — a timeout, a transport failure or a 5xx proves
    nothing either way. A 401, 403 or 429 decides only when the adapter
    reads it as the client's or the project's refusal, and then it raises
    (KAN-780): `invalid_client` at any status, a `PROJECT_REFUSALS` reason on
    a 401 or 403 only (a 429 is otherwise transient). An `UNAUTHENTICATED`
    (the token refused before Google judges the project), a 429 whose reason
    is no known quota (`RESOURCE_EXHAUSTED`) or a 403 `PERMISSION_DENIED`
    says nothing about the client and never clears a refusal. What clears one is
    a success or a 400 / 404 / 422, Google answering for the request itself.
    A token-endpoint row decides only when it is
    `invalid_client`: a token granted says nothing about the project (Google
    reports a disabled API only on the API itself), and a refused grant is the
    binding's."""

    return (
        IntegrationCallLog.http_status.is_not(None),
        IntegrationCallLog.http_status < 500,
        or_(IntegrationCallLog.provider_error.is_(None), IntegrationCallLog.provider_error.not_in(QUOTA_REASONS)),
        or_(
            IntegrationCallLog.http_status.not_in(_UNJUDGED_STATUSES),
            # As the adapter maps them (`_raise_for_status`): `invalid_client`
            # at any status is the client refused; a project reason only on a
            # 401 / 403 — a 429 is otherwise transient.
            IntegrationCallLog.provider_error == "invalid_client",
            and_(IntegrationCallLog.http_status != 429, IntegrationCallLog.provider_error.in_(PROJECT_REFUSALS)),
        ),
        or_(
            IntegrationCallLog.capability.not_in(TOKEN_OPERATIONS),
            IntegrationCallLog.provider_error == "invalid_client",
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
        select(IntegrationCallLog.provider_error, IntegrationCallLog.created_at)
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
    refusal = deciding.provider_error if deciding is not None and deciding.provider_error in CLIENT_REFUSALS else None
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


DRIFT_SOURCE = "google_business_profile_drift"
DRIFT_ITEM_PREFIX = "google_business_profile.drift."
DRIFT_LINK = "/administration/integrations"
_DRIFT_MESSAGE = {
    GbpDriftOutcome.IN_SYNC: "inSync",
    GbpDriftOutcome.DRIFT: "drift",
    GbpDriftOutcome.FAILED: "checkFailed",
}


class GbpDriftStatusProvider:
    """KAN-226 PR-4: one item per active binding the nightly drift job has
    checked (`gbp_drift`) — ok when Google shows what Nexotec pushes, a
    warning naming the location when a pushed field differs at Google or the
    check could not read it, linked to the integrations screen, whose card
    names the fields. A binding never checked yet, a skipped check (a
    deactivated location, ADR-199) and a binding that is not active have no
    item. Derived from the stored check on every read; writes nothing."""

    source = DRIFT_SOURCE

    def items(self, db: Session, *, dealership_id: uuid.UUID, now: dt.datetime) -> list[StatusItem]:
        rows = db.execute(
            select(
                GbpLocationBinding.id,
                GbpLocationBinding.location_label,
                GbpDriftCheck.outcome,
                GbpDriftCheck.drifted_fields,
            )
            .join(GbpDriftCheck, GbpDriftCheck.binding_id == GbpLocationBinding.id)
            .where(
                GbpLocationBinding.tenant_id == dealership_id,
                GbpDriftCheck.tenant_id == dealership_id,
                GbpLocationBinding.status == GbpBindingStatus.ACTIVE,
                GbpDriftCheck.outcome != GbpDriftOutcome.SKIPPED,
            )
            .order_by(TextSortKey(GbpLocationBinding.location_label), GbpLocationBinding.id)
        ).all()
        items: list[StatusItem] = []
        for binding_id, label, outcome, fields in rows:
            params: dict[str, str | int] = {"location": label}
            if outcome == GbpDriftOutcome.DRIFT:
                params["count"] = len(fields or [])
            items.append(
                StatusItem(
                    key=f"{DRIFT_ITEM_PREFIX}{binding_id}",
                    source=DRIFT_SOURCE,
                    state=StatusState.OK if outcome == GbpDriftOutcome.IN_SYNC else StatusState.WARNING,
                    message_key=f"statusPage.items.googleBusinessProfileDrift.{_DRIFT_MESSAGE[outcome]}",
                    link=DRIFT_LINK,
                    params=params,
                )
            )
        return items
