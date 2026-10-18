"""The identity front door (KAN-196; Authentication & Identity, D-A-07):
Nexotec owns login. `POST /v1/auth/login` takes e-mail and password and
answers either the session (the same httpOnly + Secure + SameSite=strict
cookie holding OUR OWN RS256 JWT as before — the session layer is unchanged,
A-RULE-AU-1) or an MFA step; the MFA step mints the session once a code, a
recovery code or a passkey is verified. Reset and invitation set a password
through a single-use token and never say whether an address exists.

`_mint_session` stays the one path every session token is minted through
(KAN-141): login, the MFA step, invitation acceptance and switch-dealership.
It now also writes the `session` row the platform session guard checks every
request against (FR-AU-17/18), and refuses a suspended or deactivated user.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.auth import (
    SESSION_COOKIE_NAME,
    AccessRole,
    Principal,
    create_access_token,
    get_current_principal,
)
from app.core.config import get_settings
from app.core.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import ModuleCode
from app.db import get_db
from app.platform.models.auth_event import AuthEventKind
from app.platform.models.auth_session import AuthSession
from app.platform.models.auth_token import AuthTokenKind
from app.platform.models.dealership import Dealership
from app.platform.models.user import User
from app.platform.schemas.auth import (
    DealershipMembershipSummary,
    InvitationAccept,
    LoginRequest,
    LoginResponse,
    LoginStepResponse,
    LogoutResponse,
    MfaStatusRead,
    MfaVerifyRequest,
    PasskeyAssertionRequest,
    PasskeyChallengeRequest,
    PasskeyOptionsResponse,
    PasskeyRead,
    PasskeyRegisterRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RecoveryCodesRead,
    SessionList,
    SessionRead,
    SwitchDealershipRequest,
    TotpConfirmRequest,
    TotpEnrolmentRead,
)
from app.platform.schemas.user import UserRead
from app.platform.services import auth_events, auth_sessions, auth_tokens, identity, mfa
from app.platform.services import dealership as dealership_service
from app.platform.services import entitlement as entitlement_service
from app.platform.services import user as user_service
from app.platform.services.mailer import PlatformMailer, get_mailer

# The front door sets or clears the session cookie on `Response`, which the
# idempotency route class cannot replay; its POSTs are the exemptions Anto
# ratified by name (tests/architecture/test_every_post_accepts_idempotency_key.py).
router = APIRouter(tags=["auth"])
# The authenticated account routes set no cookie and take the route class.
account_router = APIRouter(tags=["auth"], route_class=IdempotentRoute)
settings = get_settings()
logger = logging.getLogger(__name__)

_COOKIE_KWARGS: dict[str, Any] = {"httponly": True, "secure": True, "samesite": "strict", "path": "/"}


def _set_session_cookie(response: Response, token: str, *, max_age: int) -> None:
    response.set_cookie(SESSION_COOKIE_NAME, token, max_age=max_age, **_COOKIE_KWARGS)


def _membership_ids(db: Session, *, user_id: uuid.UUID, home_dealership_id: uuid.UUID) -> frozenset[uuid.UUID]:
    return frozenset({home_dealership_id}) | user_service.list_membership_dealership_ids(db, user_id=user_id)


def client_ip(request: Request) -> str:
    """The source address the throttle and the auth events use. Behind the
    platform's proxy (Render) the socket peer is the proxy, which appends
    the real client to X-Forwarded-For — so the LAST entry is trusted, never
    the first, which the client itself may have written to dodge the per-IP
    backoff (FR-AU-08). The per-account axis does not depend on it."""

    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.rsplit(",", 1)[-1].strip()[:45]
    return (request.client.host if request.client else "")[:45]


def _device_summary(request: Request) -> str:
    return request.headers.get("user-agent", "")[:255]


@dataclass(frozen=True)
class _MintedSession:
    token: str
    dealership: Dealership
    membership_ids: frozenset[uuid.UUID]
    entitlements: frozenset[str]
    session: AuthSession | None
    max_age: int


def _mint_session(
    db: Session,
    *,
    user: User,
    dealership_id: uuid.UUID,
    session: AuthSession | None,
    request: Request | None = None,
    amr: tuple[str, ...] = (),
    mfa_used: bool = False,
) -> _MintedSession:
    """The one path every session token is minted through — login, the MFA
    step, invitation acceptance and switch-dealership alike (KAN-141).
    Everything is read from the database at the moment of minting, never
    from a token the caller already holds: a suspended or deactivated user
    is refused with 401, and the target must be the home dealership or a
    current dealership_membership row — 403 otherwise, checked before the
    dealership is looked up, so an unknown id reveals nothing.

    `session` is the row of the session being (re)issued: None creates one
    (a login; `request` supplies the device and IP) — a switch passes the
    caller's row and the token keeps its `sid`.
    """

    if user.status in identity._INACTIVE:
        raise UnauthorizedError("This user account is not active.")
    membership_ids = _membership_ids(db, user_id=user.id, home_dealership_id=user.tenant_id)
    if dealership_id not in membership_ids:
        raise ForbiddenError("This dealership is not one of your memberships.")
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    entitlements = entitlement_service.list_active_modules(db, dealership.id)
    if session is None and request is not None:
        session = auth_sessions.create(
            db,
            user_id=user.id,
            dealership_id=dealership.id,
            device_summary=_device_summary(request),
            ip_address=client_ip(request),
            amr=amr,
        )
    token = create_access_token(
        user_id=user.id,
        tenant_id=dealership.id,
        group_id=dealership.dealer_group_id,
        memberships=membership_ids,
        roles=frozenset(AccessRole(role) for role in user.access_roles),
        is_dealer_manager=user_service.is_dealer_manager_in(db, user=user, dealership_id=dealership.id),
        entitlements=entitlements,
        entitlements_version=dealership.entitlements_version,
        session_id=session.id if session is not None else None,
        amr=tuple(session.amr) if session is not None else amr,
        mfa=("mfa" in session.amr) if session is not None else mfa_used,
        expires_at=session.absolute_expires_at if session is not None else None,
    )
    max_age = (
        int((session.absolute_expires_at - auth_sessions.utcnow()).total_seconds())
        if session is not None
        else settings.jwt_access_token_ttl_seconds
    )
    return _MintedSession(
        token=token,
        dealership=dealership,
        membership_ids=membership_ids,
        entitlements=entitlements,
        session=session,
        max_age=max(max_age, 1),
    )


def _enrolment_required(db: Session, *, user: User, dealership_id: uuid.UUID, mfa_used: bool) -> bool:
    """D-A-36 / FR-AU-11: a session that proved no factor owes one when the
    user is platform staff, or when the Betrieb enforces it. Judged on
    the SESSION (what it proved), the same way the guard judges it — a
    factor the user enrolled from another session frees nothing here."""

    if mfa_used:
        return False
    if AccessRole.PLATFORM_ADMIN.value in user.access_roles:
        return True
    return identity.read_auth_config(db, dealership_id).mfa_enforced


def _session_proved_factor(db: Session, principal: Principal) -> bool:
    """What this session proved — the row's `amr` (a factor confirmed from
    the session joins it), or the token's claim for a session without a row."""

    row = auth_sessions.get(db, principal.session_id) if principal.session_id is not None else None
    return ("mfa" in (row.amr or [])) if row is not None else principal.mfa


def _login_response(
    db: Session,
    *,
    user: User,
    active_dealership: Dealership,
    membership_ids: frozenset[uuid.UUID],
    entitlements: frozenset[str],
    mfa_used: bool,
) -> LoginResponse:
    rows = dealership_service.list_dealerships_by_ids(db, membership_ids)
    rows.sort(key=lambda d: (d.id != active_dealership.id, d.legal_name))
    return LoginResponse(
        user=UserRead.model_validate(user, from_attributes=True),
        active_dealership=DealershipMembershipSummary.model_validate(active_dealership, from_attributes=True),
        memberships=[DealershipMembershipSummary.model_validate(d, from_attributes=True) for d in rows],
        entitlements=sorted(ModuleCode(code) for code in entitlements),
        mfa_enrolment_required=_enrolment_required(
            db, user=user, dealership_id=active_dealership.id, mfa_used=mfa_used
        ),
    )


def _finish_login(
    db: Session, request: Request, response: Response, *, user: User, amr: tuple[str, ...], mfa_used: bool
) -> LoginResponse:
    minted = _mint_session(
        db, user=user, dealership_id=user.tenant_id, session=None, request=request, amr=amr, mfa_used=mfa_used
    )
    # The account's failure counter is cleared by a COMPLETED login, never by
    # the password step alone: wrong MFA answers count on it (FR-AU-08).
    identity.login_completed(db, user=user)
    auth_events.record(
        db,
        AuthEventKind.LOGIN_SUCCEEDED,
        user_id=user.id,
        dealership_id=user.tenant_id,
        ip_address=client_ip(request),
        details={"amr": list(amr)},
    )
    db.commit()
    _set_session_cookie(response, minted.token, max_age=minted.max_age)
    return _login_response(
        db,
        user=user,
        active_dealership=minted.dealership,
        membership_ids=minted.membership_ids,
        entitlements=minted.entitlements,
        mfa_used=mfa_used,
    )


# --- login --------------------------------------------------------------------------------


@router.post("/auth/login", response_model=LoginStepResponse)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """FR-AU-07: e-mail and password in, the session cookie out — or the MFA
    step when the user holds a factor. Every refusal is the same 401, with
    the same work behind it (identity.LocalPasswordProvider)."""

    presented = identity.PresentedCredentials(email=body.email, password=body.password, ip_address=client_ip(request))
    user = identity.local_password_provider.resolve(db, presented)
    if mfa.has_factor(db, user.id):
        return _begin_mfa_step(db, user)
    session = _finish_login(db, request, response, user=user, amr=("pwd",), mfa_used=False)
    return LoginStepResponse(session=session)


def _begin_mfa_step(db: Session, user: User) -> LoginStepResponse:
    """The password is right and the user holds a factor: a 5-minute
    single-use challenge instead of a session."""

    challenge = auth_tokens.issue(db, user_id=user.id, kind=AuthTokenKind.MFA_CHALLENGE, actor_id=None)
    db.commit()
    totp = mfa.totp_of(db, user.id)
    factors = ["totp"] if totp is not None and totp.confirmed_at is not None else []
    if mfa.passkeys_of(db, user.id):
        factors.append("passkey")
    return LoginStepResponse(mfa_required=True, challenge_id=challenge, factors=factors + ["recovery_code"])


# FR-AU-08 on the second step: this many wrong answers end the challenge; the
# user starts over with the password, where the account and IP backoff apply.
MFA_MAX_ATTEMPTS = 5
_STEP_EXPIRED = "The sign-in step has expired — start again."


def _step_expired() -> UnauthorizedError:
    # `details.step` lets the screen say "start again" instead of "wrong code".
    return UnauthorizedError(_STEP_EXPIRED, details={"step": "expired"})


def _challenged_user(db: Session, challenge_id: str, *, ip_address: str = "", answer: bool = True) -> User:
    """The user behind a live challenge. With `answer`, the account's lock
    and backoff are honoured and one attempt is claimed on the challenge (a
    guarded counter, see auth_tokens.claim_attempt): the sixth answer, or
    an account that is throttled, ends the challenge and sends the user
    back to the password. Every wrong answer itself counts on the account
    (`identity.second_step_failed`, in the verify routes)."""

    token = auth_tokens.peek(db, raw=challenge_id, kind=AuthTokenKind.MFA_CHALLENGE)
    user = db.get(User, token.user_id) if token is not None else None
    if token is None or user is None:
        raise _step_expired()
    if answer and (
        identity.account_throttled(db, user=user)
        or not auth_tokens.claim_attempt(db, token, limit=MFA_MAX_ATTEMPTS)
    ):
        # The account is locked or backing off (every wrong answer here
        # counts on it, like a wrong password), or this challenge's five
        # answers are spent: the challenge ends — whichever of the parallel
        # requests ends it records that — and the user starts over.
        if auth_tokens.consume(db, raw=challenge_id, kind=AuthTokenKind.MFA_CHALLENGE) is not None:
            auth_events.record(
                db,
                AuthEventKind.LOGIN_THROTTLED,
                user_id=user.id,
                dealership_id=user.tenant_id,
                ip_address=ip_address,
                details={"step": "mfa"},
            )
            db.commit()
        raise _step_expired()
    return user


def _spend_challenge(db: Session, challenge_id: str) -> None:
    """Single use, enforced where it matters: a twin request that spent the
    challenge first leaves this one with nothing to mint from."""

    if auth_tokens.consume(db, raw=challenge_id, kind=AuthTokenKind.MFA_CHALLENGE) is None:
        raise _step_expired()


@router.post("/auth/mfa/verify", response_model=LoginStepResponse)
def verify_mfa(body: MfaVerifyRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """The second step of a login: a TOTP code or a recovery code against
    the 5-minute challenge the first step issued. A wrong code is a
    `mfa_failed` event and the same 401 for every reason."""

    ip = client_ip(request)
    user = _challenged_user(db, body.challenge_id, ip_address=ip)
    if mfa.verify_totp(db, user=user, code=body.code):
        amr: tuple[str, ...] = ("pwd", "otp", "mfa")
    elif mfa.use_recovery_code(db, user=user, code=body.code, ip_address=ip):
        amr = ("pwd", "mfa")
    else:
        identity.second_step_failed(db, user=user, ip_address=ip)
        auth_events.record(db, AuthEventKind.MFA_FAILED, user_id=user.id, dealership_id=user.tenant_id, ip_address=ip)
        db.commit()
        raise UnauthorizedError("The code is not valid.")
    _spend_challenge(db, body.challenge_id)
    session = _finish_login(db, request, response, user=user, amr=amr, mfa_used=True)
    return LoginStepResponse(session=session)


@router.post("/auth/mfa/passkey/options", response_model=PasskeyOptionsResponse)
def passkey_login_options(body: PasskeyChallengeRequest, db: Session = Depends(get_db)):
    user = _challenged_user(db, body.challenge_id, answer=False)
    options = mfa.passkey_authentication_options(db, user=user, challenge=mfa.challenge_for(body.challenge_id, "authn"))
    return PasskeyOptionsResponse(challenge_id=body.challenge_id, options=options)


@router.post("/auth/mfa/passkey/verify", response_model=LoginStepResponse)
def passkey_login_verify(
    body: PasskeyAssertionRequest, request: Request, response: Response, db: Session = Depends(get_db)
):
    ip = client_ip(request)
    user = _challenged_user(db, body.challenge_id, ip_address=ip)
    if not mfa.verify_passkey(
        db, user=user, credential=body.credential, challenge=mfa.challenge_for(body.challenge_id, "authn")
    ):
        identity.second_step_failed(db, user=user, ip_address=ip)
        auth_events.record(db, AuthEventKind.MFA_FAILED, user_id=user.id, dealership_id=user.tenant_id, ip_address=ip)
        db.commit()
        raise UnauthorizedError("The passkey could not be verified.")
    _spend_challenge(db, body.challenge_id)
    session = _finish_login(db, request, response, user=user, amr=("pwd", "hwk", "mfa"), mfa_used=True)
    return LoginStepResponse(session=session)


# --- reset and invitation ---------------------------------------------------------------


@router.post("/auth/password-reset/request", status_code=202)
def password_reset_request(
    body: PasswordResetRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    mailer: PlatformMailer = Depends(get_mailer),
):
    """FR-AU-14/16: 202 whether or not the address exists; the mail goes
    only where there is someone to send it to — and after the response, so
    a mailbox's latency never says which of the two happened."""

    ip = client_ip(request)
    pending = identity.request_password_reset(db, email=body.email, ip_address=ip)
    db.commit()
    background.add_task(identity.send_pending_mail, mailer, pending, bind=db.get_bind(), ip_address=ip)


@router.post("/auth/password-reset/confirm", status_code=204)
def password_reset_confirm(body: PasswordResetConfirm, request: Request, db: Session = Depends(get_db)):
    identity.confirm_password_reset(db, raw_token=body.token, password=body.password, ip_address=client_ip(request))
    db.commit()


@router.post("/auth/invitations/accept", response_model=LoginStepResponse)
def invitation_accept(body: InvitationAccept, request: Request, response: Response, db: Session = Depends(get_db)):
    """FR-AU-13: the invited person sets their password and is signed in,
    so they can enrol a factor in the same flow where the Betrieb enforces
    one (the response says so). A user who already holds a factor — a
    re-issued invitation — gets the MFA step, like a login."""

    user = identity.accept_invitation(db, raw_token=body.token, password=body.password, ip_address=client_ip(request))
    if mfa.has_factor(db, user.id):
        return _begin_mfa_step(db, user)
    session = _finish_login(db, request, response, user=user, amr=("pwd",), mfa_used=False)
    return LoginStepResponse(session=session)


# --- session ---------------------------------------------------------------------------


@router.post("/auth/logout", response_model=LogoutResponse)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Ends the session row too (FR-AU-18): the cookie going away is not
    enough once a copy of the token could be elsewhere."""

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        try:
            principal = get_current_principal(token)
        except UnauthorizedError:
            principal = None
        if principal is not None and principal.session_id is not None:
            row = auth_sessions.get(db, principal.session_id)
            if row is not None and row.revoked_at is None:
                auth_sessions.revoke(db, row, reason="logout")
                auth_events.record(
                    db,
                    AuthEventKind.SESSION_REVOKED,
                    user_id=principal.user_id,
                    dealership_id=principal.tenant_id,
                    ip_address=client_ip(request),
                    details={"count": 1, "reason": "logout"},
                )
                db.commit()
    response.delete_cookie(SESSION_COOKIE_NAME, **_COOKIE_KWARGS)
    return LogoutResponse()


@router.get("/auth/me", response_model=LoginResponse)
def get_current_session_user(principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)):
    """Restores "logged in as X" after a page reload. The memberships are
    read from the database (KAN-237), never the token's claim."""

    user = user_service.get_own_user_or_404(db, principal.user_id)
    dealership = dealership_service.get_dealership_or_404(db, principal.tenant_id)
    membership_ids = _membership_ids(db, user_id=user.id, home_dealership_id=user.tenant_id)
    return _login_response(
        db,
        user=user,
        active_dealership=dealership,
        membership_ids=membership_ids,
        entitlements=entitlement_service.list_active_modules(db, dealership.id),
        mfa_used=_session_proved_factor(db, principal),
    )


@router.post("/auth/switch-dealership", response_model=LoginResponse)
def switch_dealership(
    body: SwitchDealershipRequest,
    response: Response,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Re-issues the session token with a different active dealership
    (WP-3 PR-3). There is no cross-dealership WRITE, ever — to act on a
    sister dealership's data the user switches active dealership here, they
    don't gain a second simultaneous tenant_id. group_id is re-resolved from
    the TARGET dealership's own dealer_group_id, not assumed unchanged from
    the caller's current token, since a membership could in principle span
    two different groups.

    A fresh token is a renewed session, so it is minted like a login
    (KAN-141): the user's status and memberships are re-read from the
    database, never trusted from the token the caller already holds — a
    user deactivated or a membership revoked since login is refused. The
    `session` row stays the same one (the guard already checked it).
    """

    user = user_service.get_own_user_or_404(db, principal.user_id)
    row = auth_sessions.get(db, principal.session_id) if principal.session_id is not None else None
    # What the session proved is the row's (a factor confirmed from this
    # session joined it) — the token's claim is what it was minted with.
    proved = ("mfa" in (row.amr or [])) if row is not None else principal.mfa
    minted = _mint_session(
        db, user=user, dealership_id=body.dealership_id, session=row, amr=tuple(principal.amr), mfa_used=proved
    )
    _set_session_cookie(response, minted.token, max_age=minted.max_age)
    return _login_response(
        db,
        user=user,
        active_dealership=minted.dealership,
        membership_ids=minted.membership_ids,
        entitlements=minted.entitlements,
        mfa_used=proved,
    )


# --- My account: sessions (FR-AU-17) --------------------------------------------------------


@account_router.get("/auth/sessions", response_model=SessionList)
def list_my_sessions(principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)):
    now = auth_sessions.utcnow()
    rows = [row for row in auth_sessions.list_for_user(db, user_id=principal.user_id) if row.is_live(now)]
    return SessionList(
        items=[
            SessionRead(
                id=row.id,
                device_summary=row.device_summary,
                ip_address=row.ip_address,
                created_at=row.created_at,
                last_seen_at=row.last_seen_at,
                absolute_expires_at=row.absolute_expires_at,
                current=row.id == principal.session_id,
            )
            for row in rows
        ]
    )


@account_router.delete("/auth/sessions/{session_id}", status_code=204)
def revoke_my_session(
    session_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    row = auth_sessions.get(db, session_id)
    if row is None or row.user_id != principal.user_id:
        raise NotFoundError("Session not found.")
    auth_sessions.revoke(db, row, reason="user")
    auth_events.record(
        db,
        AuthEventKind.SESSION_REVOKED,
        user_id=principal.user_id,
        dealership_id=principal.tenant_id,
        ip_address=client_ip(request),
        details={"count": 1, "reason": "user"},
    )
    db.commit()


# --- My account: second factors (D-A-09, A-13) -------------------------------------------------


def _mfa_status(db: Session, *, user: User, principal: Principal) -> MfaStatusRead:
    totp = mfa.totp_of(db, user.id)
    enforced = (
        AccessRole.PLATFORM_ADMIN.value in user.access_roles
        or identity.read_auth_config(db, principal.tenant_id).mfa_enforced
    )
    return MfaStatusRead(
        totp_enrolled=totp is not None and totp.confirmed_at is not None,
        passkeys=[
            PasskeyRead(id=p.id, label=p.label, created_at=p.created_at, last_used_at=p.last_used_at)
            for p in mfa.passkeys_of(db, user.id)
        ],
        recovery_codes_remaining=mfa.unused_recovery_codes(db, user.id),
        enforced=enforced,
        enrolment_required=_enrolment_required(
            db, user=user, dealership_id=principal.tenant_id, mfa_used=_session_proved_factor(db, principal)
        ),
    )


@account_router.get("/auth/mfa", response_model=MfaStatusRead)
def mfa_status(principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)):
    user = user_service.get_own_user_or_404(db, principal.user_id)
    return _mfa_status(db, user=user, principal=principal)


@account_router.post("/auth/mfa/totp/enrol", response_model=TotpEnrolmentRead)
def totp_enrol(principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)):
    user = user_service.get_own_user_or_404(db, principal.user_id)
    enrolment = mfa.begin_totp(db, user=user)
    db.commit()
    return TotpEnrolmentRead(secret=enrolment.secret, provisioning_uri=enrolment.provisioning_uri)


@account_router.post("/auth/mfa/totp/confirm", response_model=RecoveryCodesRead)
def totp_confirm(
    body: TotpConfirmRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Confirms the enrolment and records the factor on this session: the
    code just proved possession, so the session that enrolled it is no
    longer one that owes a factor (the guard would otherwise confine the
    user until they signed out)."""

    user = user_service.get_own_user_or_404(db, principal.user_id)
    codes = mfa.confirm_totp(db, user=user, code=body.code, ip_address=client_ip(request))
    _record_factor_on_session(db, principal=principal, method="otp")
    db.commit()
    return RecoveryCodesRead(codes=codes)


def _record_factor_on_session(db: Session, *, principal: Principal, method: str) -> None:
    """The session row gains the factor (`amr` + "mfa"). The guard judges
    the row, not the token, so the session that enrolled is no longer
    confined; the token's own `mfa` claim stays as minted — a staff member
    reaches the recovery routes with their next sign-in. The account routes
    are idempotent routes, which cannot re-issue the cookie; a switch of
    dealership re-mints from the row and carries the factor from then on.
    A session minted by a test token (no row) is left alone."""

    row = auth_sessions.get(db, principal.session_id) if principal.session_id is not None else None
    if row is None:
        return
    row.amr = sorted(set(row.amr) | {method, "mfa"})
    db.flush()


@account_router.delete("/auth/mfa/totp", status_code=204)
def totp_remove(request: Request, principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)):
    user = user_service.get_own_user_or_404(db, principal.user_id)
    mfa.remove_totp(db, user=user, actor_id=principal.user_id, ip_address=client_ip(request))
    _drop_factor_from_session_if_none_left(db, principal=principal, user=user)
    db.commit()


def _drop_factor_from_session_if_none_left(db: Session, *, principal: Principal, user: User) -> None:
    """The inverse of `_record_factor_on_session`: a session whose user just
    removed their last factor proved nothing any more — it owes enrolment
    again (the guard and /auth/me agree on the row)."""

    if mfa.has_factor(db, user.id):
        return
    row = auth_sessions.get(db, principal.session_id) if principal.session_id is not None else None
    if row is not None:
        row.amr = sorted(set(row.amr) - {"mfa", "otp", "hwk"})
        db.flush()
    # The user's OTHER sessions proved a factor that no longer exists: they
    # end (recorded), and their next sign-in owes enrolment like this one.
    ended = auth_sessions.revoke_all_for_user(
        db, user_id=user.id, reason="mfa_removed", except_session_id=row.id if row is not None else None
    )
    if ended:
        auth_events.record(
            db,
            AuthEventKind.SESSION_REVOKED,
            user_id=user.id,
            dealership_id=user.tenant_id,
            actor_id=user.id,
            details={"count": ended, "reason": "mfa_removed"},
        )


@account_router.post("/auth/mfa/recovery-codes/regenerate", response_model=RecoveryCodesRead)
def recovery_codes_regenerate(
    request: Request, principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    user = user_service.get_own_user_or_404(db, principal.user_id)
    codes = mfa.regenerate_recovery_codes(db, user=user, ip_address=client_ip(request))
    db.commit()
    return RecoveryCodesRead(codes=codes)


@account_router.post("/auth/mfa/passkeys/options", response_model=PasskeyOptionsResponse)
def passkey_register_options(principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)):
    user = user_service.get_own_user_or_404(db, principal.user_id)
    challenge_id = auth_tokens.issue(db, user_id=user.id, kind=AuthTokenKind.PASSKEY_REGISTRATION, actor_id=user.id)
    options = mfa.passkey_registration_options(db, user=user, challenge=mfa.challenge_for(challenge_id, "register"))
    db.commit()
    return PasskeyOptionsResponse(challenge_id=challenge_id, options=options)


@account_router.post("/auth/mfa/passkeys", response_model=PasskeyRead, status_code=201)
def passkey_register(
    body: PasskeyRegisterRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    user = user_service.get_own_user_or_404(db, principal.user_id)
    token = auth_tokens.peek(db, raw=body.challenge_id, kind=AuthTokenKind.PASSKEY_REGISTRATION)
    if token is None or token.user_id != user.id:
        raise UnauthorizedError("The passkey step has expired — start again.")
    row = mfa.register_passkey(
        db,
        user=user,
        credential=body.credential,
        challenge=mfa.challenge_for(body.challenge_id, "register"),
        label=body.label,
        ip_address=client_ip(request),
    )
    # Spent on success only, like the login step's challenge: a cancelled
    # browser dialog may be retried while the five minutes last.
    if auth_tokens.consume(db, raw=body.challenge_id, kind=AuthTokenKind.PASSKEY_REGISTRATION) is None:
        raise UnauthorizedError("The passkey step has expired — start again.")
    _record_factor_on_session(db, principal=principal, method="hwk")
    db.commit()
    return PasskeyRead(id=row.id, label=row.label, created_at=row.created_at, last_used_at=row.last_used_at)


@account_router.delete("/auth/mfa/passkeys/{passkey_id}", status_code=204)
def passkey_remove(
    passkey_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    user = user_service.get_own_user_or_404(db, principal.user_id)
    if not mfa.remove_passkey(
        db, user=user, passkey_id=passkey_id, actor_id=principal.user_id, ip_address=client_ip(request)
    ):
        raise NotFoundError("Passkey not found.")
    _drop_factor_from_session_if_none_left(db, principal=principal, user=user)
    db.commit()
