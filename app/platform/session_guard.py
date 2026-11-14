"""The session guard (FR-AU-17/18, KAN-196): an app-level dependency that
checks every request carrying one of our tokens against its `session` row
before the route runs. A token without `sid` (a test token, bootstrap) is
left to the route; a token whose row is revoked, idle beyond its timeout or
past its absolute end is 401 — the cookie is cleared by the client on 401
(KAN-237), so the user lands on the login screen. Activity extends the idle
end (`auth_sessions.touch`); a background request (`X-DMS-Background`, the
bell's poll — KAN-216) is checked but is not activity. A malformed or expired token is not judged
here: the route's own `get_current_principal` answers it, so an
unauthenticated route stays reachable with a stale cookie in the jar."""

import uuid

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import SESSION_COOKIE_NAME, AccessRole, decode_token_claims
from app.core.base import utcnow
from app.core.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.db import get_db
from app.platform.models.auth_event import AuthEventKind
from app.platform.models.auth_session import AuthSession
from app.platform.models.dealership_auth_config import DealershipAuthConfig
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.services import auth_events, auth_sessions, legal_documents, mfa

_SESSION_ENDED = "Your session has ended — sign in again."
_ENROL_FIRST = "Enrol a second factor before anything else — your business requires one."
# What a session minted without a required second factor may still reach
# (D-A-36, FR-AU-11): its own identity, the enrolment itself, its sessions,
# the UI preferences the shell loads, and the way out.
# Everything under /v1/auth (the front door is unauthenticated anyway; the
# enrolment, the sessions, logout) and the preferences the shell loads.
_ENROLMENT_PATHS = ("/v1/auth/", "/v1/me/preferences")


def _enrolment_path(path: str, claims: dict) -> bool:
    if not path.startswith("/v1/"):
        return True  # health, the SPA's own files — not the API's business
    if path.startswith(_ENROLMENT_PATHS):
        return True
    # The Betrieb's OWN enforcement switch stays reachable to its manager: a
    # manager who decides against a second factor turns it off instead of
    # enrolling. Never for a staff session (enforced whatever the switch
    # says) and never another Betrieb's.
    own_switch = f"/v1/dealerships/{claims.get('tenant_id')}/auth-config"
    return path == own_switch and AccessRole.PLATFORM_ADMIN.value not in (claims.get("roles") or ())


def _enrolment_owed(db: Session, claims: dict, row: AuthSession) -> bool:
    """A session with no second factor while one is required: every platform
    staff account (D-A-36); a dealer user when the Betrieb enforces it
    (default on — a Betrieb without a config row yet enforces). Judged on
    the session ROW's `amr`, which a factor confirmed from this session
    joins at once — the token's claim is what it was minted with."""

    if "mfa" in (row.amr or []):
        return False
    if AccessRole.PLATFORM_ADMIN.value in (claims.get("roles") or ()):
        return True
    enforced = db.scalar(
        select(DealershipAuthConfig.mfa_enforced).where(DealershipAuthConfig.dealership_id == uuid.UUID(claims["tenant_id"]))
    )
    return True if enforced is None else bool(enforced)


# KAN-216: a request the client makes without the user doing anything — a
# poll — says so with this header. The guard still checks the session is
# live; it does not extend the idle end. The header can only shorten a
# session, never lengthen one, so a client that sends it wrongly harms
# nobody but itself.
BACKGROUND_REQUEST_HEADER = "X-DMS-Background"


def is_background_request(request: Request) -> bool:
    return request.headers.get(BACKGROUND_REQUEST_HEADER, "").strip().lower() in {"1", "true", "poll"}


def _token_from(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return request.cookies.get(SESSION_COOKIE_NAME)


def enforce_live_session(request: Request, db: Session = Depends(get_db)) -> None:
    token = _token_from(request)
    if not token:
        return
    if request.url.path.endswith("/auth/logout"):
        # Signing out of a session that has already ended is the one thing a
        # dead session may still do; the route clears the cookie either way.
        return
    try:
        claims = decode_token_claims(token)
    except UnauthorizedError:
        return  # the route decides; an unauthenticated route must not 401 on a stale cookie
    sid = claims.get("sid")
    if not sid:
        return
    try:
        session_id = uuid.UUID(str(sid))
    except ValueError:
        raise UnauthorizedError(_SESSION_ENDED) from None
    row = auth_sessions.get(db, session_id)
    if row is None or not row.is_live(utcnow()):
        raise UnauthorizedError(_SESSION_ENDED)
    # A request the SPA makes on its own (the bell's unread count, KAN-216)
    # is judged like any other but is not activity: an unattended tab must
    # still reach its idle end (FR-AU-19).
    if not is_background_request(request) and auth_sessions.touch(db, row):
        db.commit()
    path = request.url.path
    if "mfa" not in (row.amr or []) and mfa.has_factor(db, row.user_id):
        # The user enrolled a factor from another session: this one never
        # proved it and must not keep working on the password alone — nor
        # mint recovery codes. It ends; the SPA goes to sign-in on the 401.
        # Re-read first: the session that is confirming its own factor right
        # now has just written "mfa" onto this row.
        db.refresh(row)
        if "mfa" not in (row.amr or []):
            auth_sessions.revoke(db, row, reason="factor_enrolled")
            auth_events.record(
                db,
                AuthEventKind.SESSION_REVOKED,
                user_id=row.user_id,
                dealership_id=row.dealership_id,
                details={"count": 1, "reason": "factor_enrolled"},
            )
            db.commit()
            raise UnauthorizedError(_SESSION_ENDED)
    if not _enrolment_path(path, claims) and _enrolment_owed(db, claims, row):
        raise ForbiddenError(_ENROL_FIRST, details={"mfaEnrolmentRequired": True})
    enforce_activation_and_legal_acceptance(request, db, claims)


# --- KAN-214: the activation gate and the legal hold -------------------------------------------

# D-A-21: a Betrieb being set up reaches Dealer Administration and My account
# and nothing else. Both are platform's API, plus the Betrieb's integration
# connections (FR-A-19: Integrations is a section of the panel). Judged by
# the module of the route's endpoint, so a new module's router is gated the
# day it is added — nothing to remember.
_ADMINISTRATION_MODULE_PREFIXES = (
    "app.platform.",
    "app.integration.api.connections",
    "app.integration.api.providers",
)
_ACCEPT_FIRST = "Accept the current Terms of Service and Data Processing Agreement first."
# What a manager held by the legal hold may still reach (FR-A-39, Q1 A).
_LEGAL_HOLD_PATHS = ("/v1/auth/", "/v1/me", "/v1/legal-documents", "/v1/i18n/")


def _administration_route(request: Request) -> bool:
    endpoint = request.scope.get("endpoint")
    module = getattr(endpoint, "__module__", "") or ""
    return module.startswith(_ADMINISTRATION_MODULE_PREFIXES)


def _legal_hold_path(path: str, tenant_id: str) -> bool:
    return path.startswith(_LEGAL_HOLD_PATHS) or path == f"/v1/dealerships/{tenant_id}/legal-acceptances"


def enforce_activation_and_legal_acceptance(request: Request, db: Session, claims: dict) -> None:
    """KAN-214, judged for a live session like the enrolment gate above (a
    token without `sid` — a test token, bootstrap — is left to the route;
    every production token has one, `_mint_session`):

    - **Activation gate (D-A-21).** While the session's Betrieb is
      `pending_onboarding`, every route outside Dealer Administration and My
      account answers 404 — nothing confirms the module exists, as the
      entitlement gate does. The SPA shows the manager the checklist and
      everyone else «Betrieb wird eingerichtet».
    - **Legal hold (FR-A-39; Anto, 2026-11-13, Q1 A).** In an active Betrieb
      whose grace for a new ToS or DPA version has passed, a manager reaches
      only the acceptance (403 `details.legalAcceptanceRequired`, like the
      enrolment gate: the user and the resource are known). Colleagues keep
      working; before the grace ends nobody is held — the SPA prompts the
      manager at every login.

    Platform staff are never gated: they set the Betrieb up with its
    manager. Signing out stays possible (the session guard returned before)."""

    path = request.url.path
    if not path.startswith("/v1/") or AccessRole.PLATFORM_ADMIN.value in (claims.get("roles") or ()):
        return
    try:
        tenant_id = uuid.UUID(str(claims.get("tenant_id")))
    except ValueError:
        return
    dealership = db.get(Dealership, tenant_id)
    if dealership is None:
        return
    if dealership.status == DealershipStatus.PENDING_ONBOARDING:
        if not _administration_route(request):
            raise NotFoundError("Not found.")
        return
    if (
        dealership.status == DealershipStatus.ACTIVE
        and claims.get("is_dealer_manager")
        and not _legal_hold_path(path, str(tenant_id))
        and legal_documents.acceptance_state(db, dealership).held
    ):
        raise ForbiddenError(_ACCEPT_FIRST, details={"legalAcceptanceRequired": True})


def enforce_open_dealership(request: Request, db: Session = Depends(get_db)) -> None:
    """The archive gate (KAN-218; FR-A-45; Anto, 2026-11-13, Q2 B): an
    offboarded Betrieb's data is a read-only archive by construction, not
    by convention. Every API request carrying a token whose Betrieb
    (`tenant_id`) is offboarded is answered 404 — everywhere, before any
    route runs: there is no dealer login after offboarding, and a 404
    confirms nothing. A former manager's copy of the archive is staff's
    archive export (`POST /v1/platform/dealerships/{id}/exports`).

    Offboarding deactivates the users and revokes their sessions, so the
    session guard above already ends a minted session; this gate holds for
    every other token too. A malformed token is left to the route; a token
    naming no Betrieb is not this gate's. Signing out stays possible."""

    path = request.url.path
    if not path.startswith("/v1/") or path.endswith("/auth/logout"):
        return
    token = _token_from(request)
    if not token:
        return
    try:
        tenant_id = uuid.UUID(str(decode_token_claims(token).get("tenant_id")))
    except (UnauthorizedError, ValueError):
        return  # the route decides
    status = db.scalar(select(Dealership.status).where(Dealership.id == tenant_id))
    if status == DealershipStatus.OFFBOARDED:
        raise NotFoundError("Not found.")
