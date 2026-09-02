"""IntegrationSecretRef (WP-6 PR-1) — one row per secret slot on a
connection. `secret_ref` is a POINTER (an Infisical secret path/name),
never a value — the actual material lives in Infisical
(services/secrets_backend.py), enforced structurally by this column never
being writable to anything but a string the resolver can look up with.

Multiple rows per connection is the norm, not the exception: auto-i-dat
needs two (`password` + `aes_key`); a future OAuth provider needs three
(`client_secret` + `refresh_token` + ...).
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin
from app.core.types import GUID, UTCDateTime
from app.db import Base


class SecretSlot(str, enum.Enum):
    PASSWORD = "password"
    AES_KEY = "aes_key"
    CLIENT_SECRET = "client_secret"
    REFRESH_TOKEN = "refresh_token"
    CERTIFICATE = "certificate"


class IntegrationSecretRef(PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_secret_ref"
    __table_args__ = (UniqueConstraint("connection_id", "slot", name="uq_integration_secret_ref_connection_slot"),)

    connection_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("integration_connection.id"), nullable=False, index=True
    )
    slot: Mapped[SecretSlot] = mapped_column(SAEnum(SecretSlot, native_enum=False, length=16), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    rotated_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
