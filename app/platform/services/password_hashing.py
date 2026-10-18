"""argon2id password hashing (KAN-196, D-A-12; Authentication & Identity,
Credentials). The only module that touches a password hash. Parameters come
from settings and are stored in the encoded hash itself, so raising the
policy never invalidates anyone: `needs_rehash` says a stored hash is below
policy and the login path rehashes it after a successful verify. Verify is
the library's constant-time compare — never `==`.

`DUMMY_HASH` is a real argon2id hash of a random secret, verified when the
presented e-mail names no account (FR-AU-07): the login then does the same
work whether or not the account exists, so its timing says nothing.
"""

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

_settings = get_settings()

_hasher = PasswordHasher(
    time_cost=_settings.argon2_time_cost,
    memory_cost=_settings.argon2_memory_kib,
    parallelism=_settings.argon2_parallelism,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """True when the password matches; False for a mismatch or a hash the
    library cannot read. Constant time for the mismatch case."""

    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True


DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def burn_verify() -> None:
    """The work of a verify, for a login that names no account."""

    verify_password(DUMMY_HASH, "not-the-dummy-secret")
