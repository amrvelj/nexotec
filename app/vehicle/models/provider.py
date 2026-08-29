"""Provider abstraction (WP-5 PR-2): the mapping layer that keeps every
provider code out of application code. No provider calls are made from
here — that is WP-6's provider-gateway job; these three tables exist so
WP-6 has somewhere correct to write its results, and so PR-1's catalogue
and PR-3's physical vehicle can already speak in canonical codes today,
with manual entry (FR-V-03), before any provider integration exists.

Reading rule (PRD §Provider abstraction): application code never sees a
provider code. It reads a canonical reference_value.value_code, resolved
through ProviderCodeMap. Anything that doesn't resolve is written to
MappingGap, never silently dropped, and surfaces in PR-8's admin queue.
"""

import datetime as dt
import uuid

from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class ProviderCodeMap(PrimaryKeyMixin, TimestampMixin, Base):
    """provider code -> canonical reference_value.value_code, PER
    vehicle_kind. The vehicle_kind qualifier is load-bearing, not
    decorative: auto-i-dat's Treibstoff=3 means Diesel for a car
    (CodeGrpNr 011) and Bleifrei Kat. for a motorcycle (CodeGrpNr 111) —
    the same (provider, code_group, code) triple means two different
    canonical values depending on what kind of vehicle it was read for.
    """

    __tablename__ = "vehicle_provider_code_map"
    __table_args__ = (
        UniqueConstraint(
            "provider", "vehicle_kind", "code_group", "provider_code",
            name="uq_vehicle_provider_code_map_natural_key",
        ),
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    vehicle_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    code_group: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    # The canonical list + value this resolves to — both named explicitly
    # rather than just a value_code string, since the same code string can
    # legitimately exist in more than one reference_list.
    canonical_list_code: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_value_code: Mapped[str] = mapped_column(String(64), nullable=False)


class ProviderEntityRef(PrimaryKeyMixin, TimestampMixin, Base):
    """Links any catalogue row to its identifier at each provider. One
    catalogue entity carries several of these at once (ADR-020) — a
    ModelVariant has an auto-i-dat FzKey row, a EurotaxCode row and a
    DATEuroCode row, captured from day one even though only auto-i-dat is
    actually integrated (WP-6). entity_type + entity_id rather than a
    dedicated FK column per catalogue table, since PR-1's five catalogue
    tables (and possibly more later) are all valid targets and this table
    must not need a schema change every time one is added.
    """

    __tablename__ = "vehicle_provider_entity_ref"
    __table_args__ = (
        UniqueConstraint(
            "entity_type", "entity_id", "provider", name="uq_vehicle_provider_entity_ref_natural_key"
        ),
    )

    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)


class MappingGap(PrimaryKeyMixin, TimestampMixin, Base):
    """Anything a provider returned that ProviderCodeMap could not resolve
    — written here, never silently dropped, and surfaced as an admin task
    (PR-8). One row per distinct (provider, vehicle_kind, code_group,
    provider_code) miss — a second identical miss touches the same row's
    last_seen_at rather than creating a duplicate, so the admin queue shows
    "this has happened N times", not N separate tasks for the same gap.

    TimestampMixin (created_at/updated_at) alongside first_seen_at/
    last_seen_at is deliberate, not redundant: created_at/updated_at are
    the generic cross-cutting audit columns every table gets (and what
    app.core.pagination's cursor pagination requires — PR-8's admin queue
    list endpoint needs it); first_seen_at/last_seen_at are this table's
    own domain fact about the gap itself.
    """

    __tablename__ = "vehicle_mapping_gap"
    __table_args__ = (
        UniqueConstraint(
            "provider", "vehicle_kind", "code_group", "provider_code",
            name="uq_vehicle_mapping_gap_natural_key",
        ),
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    vehicle_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    code_group: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    first_seen_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    last_seen_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    resolved_value_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
