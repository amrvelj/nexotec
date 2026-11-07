"""IntegrationProvider (WP-6 PR-1) — the catalogue of supported providers,
maintained by platform staff (mirrors `app.vehicle.models.catalogue.Brand`'s
own "platform-authored, versioned" posture). Global: a provider definition
is not tenant data — `auto-i-dat` is the same catalogue entry regardless of
which dealer eventually connects to it.
"""

from sqlalchemy import JSON, Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.db import Base


class IntegrationProvider(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "integration_provider"

    provider_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)  # vehicle_data | marketplace | ...
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # e.g. ["client_secret"] for auto-i-dat's identAPI, ["password"] for
    # AutoScout24 — the slots an account needs are data, not code
    # (Integrations & API Credentials v0.1), so a provider needing three
    # slots is a catalogue row, never a schema change.
    required_secret_slots: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # The non-secret counterpart to required_secret_slots (KAN-36) — e.g.
    # ["clientId"] for auto-i-dat's identAPI (KAN-488). Without
    # this, a provider-specific required config field (an account number,
    # a project label) has no way to be validated at connection-creation
    # time or rendered as a form field in the dealer's own "Connect"
    # dialog — the same "a catalogue row, never a schema change" posture
    # as required_secret_slots, just for `IntegrationConnection.config`.
    required_config_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    capability_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    docs_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    supports_sandbox: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
