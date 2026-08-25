"""Portable column types shared by every model.

GUID stores as native UUID on Postgres and CHAR(36) text elsewhere, so the
same model definitions work against the SQLite test database and the
Postgres production database without dialect-specific model code.
"""

import datetime as dt
import uuid
from functools import lru_cache

from cryptography.fernet import Fernet
from sqlalchemy import CHAR, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import TypeDecorator

from app.core.config import get_settings


class GUID(TypeDecorator):
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return str(value)
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


class UTCDateTime(TypeDecorator):
    """DateTime(timezone=True) that always round-trips as UTC-aware in
    Python, on every backend. SQLite drops tzinfo on read even for a
    timezone=True column (Postgres's TIMESTAMP WITH TIME ZONE preserves it)
    — found via issue #7's acceptance test for "all timestamps are UTC
    ISO-8601" (spec AC9), which failed under SQLite because a naive
    datetime serializes with no UTC offset at all. Normalizing at the type
    level, here, closes the gap for every entity at once instead of the
    ad hoc per-call workaround this codebase already had in
    services/auth.py (_as_aware_utc, for Credential.locked_until) — that
    one is still harmless to leave as defensive code, just no longer the
    only fix. Bind side normalizes naive input to UTC too, so a caller
    passing a naive datetime doesn't silently get local-time semantics.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC)

    def process_result_value(self, value: dt.datetime | None, dialect) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC)


@lru_cache
def _fernet() -> Fernet:
    return Fernet(get_settings().tax_id_encryption_key.encode("ascii"))


class EncryptedString(TypeDecorator):
    """Field-level encryption at rest (Fernet/AES-128-CBC+HMAC) for columns
    like Dealership.tax_id. Interim mechanism: a single static key from settings,
    not a KMS-backed per-tenant key — see the key-management note on
    `Settings.tax_id_encryption_key`. Encrypted ciphertext is base64 text,
    portable across SQLite (tests) and Postgres without dialect branches.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return _fernet().encrypt(value.encode("utf-8")).decode("ascii")

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
