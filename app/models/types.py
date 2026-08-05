"""Portable column types shared by every model.

GUID stores as native UUID on Postgres and CHAR(36) text elsewhere, so the
same model definitions work against the SQLite test database and the
Postgres production database without dialect-specific model code.
"""

import uuid
from functools import lru_cache

from cryptography.fernet import Fernet
from sqlalchemy import CHAR, Text
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


@lru_cache
def _fernet() -> Fernet:
    return Fernet(get_settings().tax_id_encryption_key.encode("ascii"))


class EncryptedString(TypeDecorator):
    """Field-level encryption at rest (Fernet/AES-128-CBC+HMAC) for columns
    like Dealer.tax_id. Interim mechanism: a single static key from settings,
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
