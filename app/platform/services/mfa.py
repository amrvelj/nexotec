"""Second factors (D-A-09, D-A-36, A-13, FR-AU-11/12, KAN-196): TOTP with
`pyotp`, ten hashed single-use recovery codes, passkeys with the `webauthn`
library (resident credentials). A factor counts once confirmed. A manager
removing another user's factors is a `mfa_removed` auth event with the actor
named — the `security` notification the user sees (D-A-40) hangs off that
stream."""

import dataclasses
import hashlib
import hmac
import secrets
import uuid

import pyotp
from sqlalchemy import select
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, UnprocessableEntityError
from app.platform.models.auth_event import AuthEventKind
from app.platform.models.mfa import MfaPasskey, MfaRecoveryCode, MfaTotp
from app.platform.models.user import User
from app.platform.services import auth_events

_settings = get_settings()
RECOVERY_CODE_COUNT = 10


# --- what a user holds --------------------------------------------------------------


def totp_of(db: Session, user_id: uuid.UUID) -> MfaTotp | None:
    return db.scalar(select(MfaTotp).where(MfaTotp.user_id == user_id))


def passkeys_of(db: Session, user_id: uuid.UUID) -> list[MfaPasskey]:
    return list(
        db.scalars(select(MfaPasskey).where(MfaPasskey.user_id == user_id).order_by(MfaPasskey.created_at)).all()
    )


def has_factor(db: Session, user_id: uuid.UUID) -> bool:
    totp = totp_of(db, user_id)
    return (totp is not None and totp.confirmed_at is not None) or bool(passkeys_of(db, user_id))


def unused_recovery_codes(db: Session, user_id: uuid.UUID) -> int:
    return len(
        db.scalars(
            select(MfaRecoveryCode.id).where(MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.used_at.is_(None))
        ).all()
    )


# --- TOTP ------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class TotpEnrolment:
    secret: str
    provisioning_uri: str


def begin_totp(db: Session, *, user: User) -> TotpEnrolment:
    """A fresh secret (replacing an unconfirmed one; a confirmed enrolment
    must be removed first). Shown once, as the key and the otpauth URI."""

    existing = totp_of(db, user.id)
    if existing is not None and existing.confirmed_at is not None:
        raise ConflictError("An authenticator app is already enrolled — remove it before enrolling another.")
    secret = pyotp.random_base32()
    if existing is None:
        existing = MfaTotp(user_id=user.id, secret=secret)
        db.add(existing)
    else:
        existing.secret = secret
        existing.created_at = utcnow()
    db.flush()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=_settings.webauthn_rp_name)
    return TotpEnrolment(secret=secret, provisioning_uri=uri)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace("-", "").encode("utf-8")).hexdigest()


def _new_recovery_codes(db: Session, *, user_id: uuid.UUID) -> list[str]:
    for row in db.scalars(select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)).all():
        db.delete(row)
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(5)  # 10 hex characters
        code = f"{raw[:5]}-{raw[5:]}"
        codes.append(code)
        db.add(MfaRecoveryCode(user_id=user_id, code_hash=_hash_code(code)))
    db.flush()
    return codes


def confirm_totp(db: Session, *, user: User, code: str, ip_address: str) -> list[str]:
    """The first valid code confirms the enrolment and returns the ten
    recovery codes — the one time they are shown."""

    totp = totp_of(db, user.id)
    if totp is None or totp.confirmed_at is not None:
        raise ConflictError("No authenticator enrolment is waiting for confirmation.")
    if not _verify_totp_code(totp, code):
        raise UnprocessableEntityError("The code is not valid.", details={"factor": "totp"})
    totp.confirmed_at = utcnow()
    codes = _new_recovery_codes(db, user_id=user.id)
    auth_events.record(
        db,
        AuthEventKind.MFA_ENROLLED,
        user_id=user.id,
        dealership_id=user.tenant_id,
        ip_address=ip_address,
        details={"factor": "totp"},
    )
    return codes


def _verify_totp_code(totp: MfaTotp, code: str) -> bool:
    clean = code.strip().replace(" ", "")
    if not clean.isdigit() or len(clean) != 6:
        return False
    verifier = pyotp.TOTP(totp.secret)
    now = utcnow()
    for offset in (0, -1, 1):
        step = int(now.timestamp()) // 30 + offset
        if totp.last_used_step is not None and step <= totp.last_used_step:
            continue  # RFC 6238: a code is good once
        if hmac.compare_digest(verifier.at(step * 30), clean):
            totp.last_used_step = step
            return True
    return False


def verify_totp(db: Session, *, user: User, code: str) -> bool:
    totp = totp_of(db, user.id)
    if totp is None or totp.confirmed_at is None:
        return False
    return _verify_totp_code(totp, code)


def use_recovery_code(db: Session, *, user: User, code: str, ip_address: str) -> bool:
    row = db.scalar(
        select(MfaRecoveryCode).where(
            MfaRecoveryCode.user_id == user.id,
            MfaRecoveryCode.code_hash == _hash_code(code),
            MfaRecoveryCode.used_at.is_(None),
        )
    )
    if row is None:
        return False
    row.used_at = utcnow()
    db.flush()  # the remaining count below must see this use
    auth_events.record(
        db,
        AuthEventKind.RECOVERY_CODE_USED,
        user_id=user.id,
        dealership_id=user.tenant_id,
        ip_address=ip_address,
        details={"remaining": unused_recovery_codes(db, user.id)},
    )
    return True


def regenerate_recovery_codes(db: Session, *, user: User, ip_address: str) -> list[str]:
    if not has_factor(db, user.id):
        raise ConflictError("Recovery codes belong to an enrolled second factor — enrol one first.")
    codes = _new_recovery_codes(db, user_id=user.id)
    auth_events.record(
        db,
        AuthEventKind.RECOVERY_CODES_REGENERATED,
        user_id=user.id,
        dealership_id=user.tenant_id,
        ip_address=ip_address,
    )
    return codes


def remove_totp(db: Session, *, user: User, actor_id: uuid.UUID, ip_address: str) -> None:
    totp = totp_of(db, user.id)
    if totp is None:
        return
    db.delete(totp)
    db.flush()
    auth_events.record(
        db,
        AuthEventKind.MFA_REMOVED,
        user_id=user.id,
        dealership_id=user.tenant_id,
        actor_id=actor_id,
        ip_address=ip_address,
        details={"factor": "totp", "by_self": actor_id == user.id},
    )
    _drop_recovery_codes_if_no_factor(db, user)


def _drop_recovery_codes_if_no_factor(db: Session, user: User) -> None:
    if not has_factor(db, user.id):
        for row in db.scalars(select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id)).all():
            db.delete(row)
        db.flush()


def remove_all_factors(db: Session, *, user: User, actor_id: uuid.UUID, ip_address: str) -> None:
    """A manager's or platform staff's reset (FR-AU-12, FR-AU-25): every
    factor and recovery code goes; the event names the actor."""

    removed = []
    totp = totp_of(db, user.id)
    if totp is not None:
        db.delete(totp)
        removed.append("totp")
    for passkey in passkeys_of(db, user.id):
        db.delete(passkey)
        removed.append("passkey")
    for row in db.scalars(select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id)).all():
        db.delete(row)
    db.flush()
    auth_events.record(
        db,
        AuthEventKind.MFA_REMOVED,
        user_id=user.id,
        dealership_id=user.tenant_id,
        actor_id=actor_id,
        ip_address=ip_address,
        details={"factors": removed, "by_self": actor_id == user.id, "all": True},
    )


# --- passkeys (WebAuthn) ----------------------------------------------------------------


def _user_handle(user: User) -> bytes:
    return user.id.bytes


def passkey_registration_options(db: Session, *, user: User, challenge: bytes) -> str:
    """The options JSON for navigator.credentials.create. The challenge is
    derived by the caller from a single-use token (auth_tokens), so the
    verify step recomputes it instead of storing it."""

    options = generate_registration_options(
        challenge=challenge,
        rp_id=_settings.webauthn_rp_id,
        rp_name=_settings.webauthn_rp_name,
        user_id=_user_handle(user),
        user_name=user.email,
        user_display_name=f"{user.first_name} {user.last_name}",
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.PREFERRED
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id)) for p in passkeys_of(db, user.id)
        ],
    )
    return options_to_json(options)


def register_passkey(
    db: Session, *, user: User, credential: dict, challenge: bytes, label: str, ip_address: str
) -> MfaPasskey:
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=_settings.webauthn_rp_id,
            expected_origin=_settings.webauthn_origin,
        )
    except InvalidRegistrationResponse as exc:
        raise UnprocessableEntityError("The passkey could not be verified.", details={"factor": "passkey"}) from exc
    row = MfaPasskey(
        user_id=user.id,
        credential_id=bytes_to_base64url(verified.credential_id),
        public_key=bytes_to_base64url(verified.credential_public_key),
        sign_count=verified.sign_count,
        transports=list(credential.get("response", {}).get("transports") or []),
        label=label[:100],
    )
    db.add(row)
    db.flush()
    if unused_recovery_codes(db, user.id) == 0 and totp_of(db, user.id) is None:
        _new_recovery_codes(db, user_id=user.id)
    auth_events.record(
        db,
        AuthEventKind.MFA_ENROLLED,
        user_id=user.id,
        dealership_id=user.tenant_id,
        ip_address=ip_address,
        details={"factor": "passkey"},
    )
    return row


def passkey_authentication_options(db: Session, *, user: User, challenge: bytes) -> str:
    options = generate_authentication_options(
        challenge=challenge,
        rp_id=_settings.webauthn_rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id)) for p in passkeys_of(db, user.id)
        ],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return options_to_json(options)


def verify_passkey(db: Session, *, user: User, credential: dict, challenge: bytes) -> bool:
    raw_id = credential.get("rawId") or credential.get("id")
    if not raw_id:
        return False
    row = db.scalar(select(MfaPasskey).where(MfaPasskey.user_id == user.id, MfaPasskey.credential_id == raw_id))
    if row is None:
        return False
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=_settings.webauthn_rp_id,
            expected_origin=_settings.webauthn_origin,
            credential_public_key=base64url_to_bytes(row.public_key),
            credential_current_sign_count=row.sign_count,
        )
    except InvalidAuthenticationResponse:
        return False
    row.sign_count = verified.new_sign_count
    row.last_used_at = utcnow()
    return True


def remove_passkey(db: Session, *, user: User, passkey_id: uuid.UUID, actor_id: uuid.UUID, ip_address: str) -> bool:
    row = db.get(MfaPasskey, passkey_id)
    if row is None or row.user_id != user.id:
        return False
    db.delete(row)
    db.flush()
    auth_events.record(
        db,
        AuthEventKind.MFA_REMOVED,
        user_id=user.id,
        dealership_id=user.tenant_id,
        actor_id=actor_id,
        ip_address=ip_address,
        details={"factor": "passkey", "by_self": actor_id == user.id},
    )
    _drop_recovery_codes_if_no_factor(db, user)
    return True


def challenge_for(raw_token: str, purpose: str) -> bytes:
    """The WebAuthn challenge bound to a single-use token: 32 bytes derived
    from the token and the purpose, so the options and the verify step agree
    without a stored challenge."""

    return hashlib.sha256(f"{raw_token}:{purpose}".encode()).digest()
