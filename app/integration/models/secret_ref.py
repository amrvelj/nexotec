"""IntegrationSecretRef (WP-6 PR-1) — one row per secret slot on a
connection. `secret_ref` is a POINTER (an Infisical secret path/name),
never a value — the actual material lives in Infisical
(services/secrets_backend.py), enforced structurally by this column never
being writable to anything but a string the resolver can look up with.

A connection holds one row per slot its provider needs — the identAPI one
(`client_secret`, KAN-488), the AutoScout24 feed one (`password`); a provider
with a refresh token or a certificate would hold more. The SOAP account's
`aes_key` slot left with the SOAP adapter (KAN-550, migration `db5f1414cbce`).
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base


class SecretSlot(str, enum.Enum):
    PASSWORD = "password"
    CLIENT_SECRET = "client_secret"
    REFRESH_TOKEN = "refresh_token"
    CERTIFICATE = "certificate"


class IntegrationSecretRef(PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_secret_ref"
    # A unique index, as the migration created it (ae20d82b02f4; KAN-170).
    __table_args__ = (Index("uq_integration_secret_ref_connection_slot", "connection_id", "slot", unique=True),)

    connection_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("integration_connection.id"), nullable=False, index=True
    )
    slot: Mapped[SecretSlot] = mapped_column(StoredEnum(SecretSlot, length=16), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    rotated_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
