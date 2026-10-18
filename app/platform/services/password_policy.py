"""Password policy (FR-AU-04…06, KAN-196): at least 12 characters, no
composition rules, no rotation; the dealership's name, the user's own name
and the local part of their e-mail are refused; the Have I Been Pwned range
check (k-anonymity: five hash characters leave, never the password) refuses
a breached password and fails OPEN when the service does not answer — the
set succeeds and is flagged, because a third party's bad afternoon must
never stop a dealer from setting a password.
"""

import dataclasses
import hashlib
import logging
from collections.abc import Callable

import httpx

from app.core.config import get_settings
from app.core.errors import UnprocessableEntityError

logger = logging.getLogger(__name__)
_settings = get_settings()

MIN_LENGTH = 12
_HIBP_RANGE_URL = "https://api.pwnedpasswords.com/range/"


@dataclasses.dataclass(frozen=True)
class PolicyVerdict:
    # True when the breach check could not be made and the password was
    # accepted on the strength of the other rules alone (FR-AU-05).
    breach_check_pending: bool


def _deny_terms(*, dealership_names: tuple[str, ...], first_name: str, last_name: str, email: str) -> list[str]:
    local_part = email.split("@", 1)[0]
    terms = [*dealership_names, first_name, last_name, local_part]
    return [term.strip().lower() for term in terms if term and len(term.strip()) >= 3]


def hibp_breach_count(password: str, *, timeout: float) -> int | None:
    """How often the password appears in HIBP's corpus; None when the
    service could not be asked (timeout, transport error, non-200)."""

    digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    try:
        response = httpx.get(
            _HIBP_RANGE_URL + prefix, timeout=timeout, headers={"Add-Padding": "true", "User-Agent": "nexotec-dms"}
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    for line in response.text.splitlines():
        candidate, _, count = line.strip().partition(":")
        if candidate.upper() == suffix:
            try:
                return int(count)
            except ValueError:
                return 0
    return 0


def validate_new_password(
    password: str,
    *,
    dealership_names: tuple[str, ...],
    first_name: str,
    last_name: str,
    email: str,
    breach_check: Callable[..., int | None] | None = None,
) -> PolicyVerdict:
    """Raises UnprocessableEntityError with a plain explanation (never the
    password) when the policy refuses it. `breach_check` defaults to HIBP,
    resolved at call time so a test can patch `hibp_breach_count`."""

    if len(password) < MIN_LENGTH:
        raise UnprocessableEntityError(
            f"The password must have at least {MIN_LENGTH} characters.",
            details={"rule": "min_length", "minLength": MIN_LENGTH},
        )
    lowered = password.lower()
    for term in _deny_terms(dealership_names=dealership_names, first_name=first_name, last_name=last_name, email=email):
        if term in lowered:
            raise UnprocessableEntityError(
                "The password must not contain your name, your e-mail address or the name of your business.",
                details={"rule": "deny_list"},
            )
    if not _settings.hibp_enabled:
        return PolicyVerdict(breach_check_pending=False)
    check = breach_check if breach_check is not None else hibp_breach_count
    count = check(password, timeout=_settings.hibp_timeout_seconds)
    if count is None:
        logger.warning("password_breach_check_unavailable")
        return PolicyVerdict(breach_check_pending=True)
    if count > 0:
        raise UnprocessableEntityError(
            "This password appears in a known data breach — choose another one.",
            details={"rule": "breached"},
        )
    return PolicyVerdict(breach_check_pending=False)
