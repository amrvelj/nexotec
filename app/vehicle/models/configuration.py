"""The configuration — a structured, priced, provider-linked description of
**one car**, with its own identity (ADR-068). C-C / KAN-41.

A configuration is **not a vehicle**. It carries the ADR-071 specification
block (mixed in from `VehicleSpecBlock` — carrier #2 alongside the
catalogue variant), plus what was observed at capture and the selected
options. It **never writes `vehicle-mdm`** (ADR-070): it links to a
physical vehicle only when a VIN resolved to an *existing* MDM row, via
the three-column pattern (`vehicle_id` + denormalised label +
`label_refreshed_at`, no FK).

It has **no standalone surface** — no list screen, no nav entry, no
human-readable number. It is always reached from a host (an offer, a
stock item, a valuation, a vehicle record), and the host freezes its own
`vehicleSnapshot` at commit while this record stays editable.

Tenant-scoped (ADR-013) — it carries licensed provider option text and
prices, and must never travel through the FR-V-14 cross-tenant identity
response.
"""

import datetime as dt
import enum
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, JSON, Boolean, Date, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.types import GUID, UTCDateTime
from app.db import Base
from app.vehicle.models.spec_block import VehicleSpecBlock


class ConfigurationSource(str, enum.Enum):
    """Which path produced the configuration. Not a quality judgement — a
    hand-built oldtimer configuration is correct data."""

    PROVIDER = "provider"
    MANUAL = "manual"


class ConfigurationMode(str, enum.Enum):
    """`build` — a new car / factory order: options are priced, packages
    apply. `record` — a used car: options are what the car *has*, recorded
    and unpriced (FR-S-07).

    Same two values as `schemas.catalogue.CatalogueBrowseMode`, deliberately
    a *separate* enum: this one is a persisted column type and lives with
    the model, so a change to the browse API's enum can never silently
    become a migration here.
    """

    BUILD = "build"
    RECORD = "record"


class ConfigurationMatchStatus(str, enum.Enum):
    """`unverified` is excluded from valuation and publishing (FR-V-03).
    `best_match_confirmed` records a `FahrzeugeMatch` `MatchCode 2` that a
    human accepted (C-D). Its own enum — `vehicle_mdm.CatalogueMatchStatus`
    has only two values and must not grow a third for this."""

    MATCHED = "matched"
    UNVERIFIED = "unverified"
    BEST_MATCH_CONFIRMED = "best_match_confirmed"


class ConfigurationMatchMethod(str, enum.Enum):
    """How the car was identified — **kept permanently**, because a later
    dispute turns on it. The full waterfall is C-D; C-C produces
    `catalogue_browse` and `manual`, and `vin` when a VIN resolved to an
    existing MDM record."""

    VIN = "vin"
    KONTROLLSCHILD = "kontrollschild"
    TYPENSCHEIN = "typenschein"
    STAMMNUMMER = "stammnummer"
    WERKSCODE = "werkscode"
    CATALOGUE_BROWSE = "catalogue_browse"
    MANUAL = "manual"


class VehicleConfiguration(
    VehicleSpecBlock, PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base
):
    __tablename__ = "vehicle_configuration"

    source: Mapped[ConfigurationSource] = mapped_column(
        SAEnum(ConfigurationSource, native_enum=False, length=16), nullable=False
    )
    mode: Mapped[ConfigurationMode] = mapped_column(
        SAEnum(ConfigurationMode, native_enum=False, length=16), nullable=False
    )
    catalogue_match_status: Mapped[ConfigurationMatchStatus] = mapped_column(
        SAEnum(ConfigurationMatchStatus, native_enum=False, length=24),
        nullable=False,
        default=ConfigurationMatchStatus.UNVERIFIED,
    )
    match_method: Mapped[ConfigurationMatchMethod] = mapped_column(
        SAEnum(ConfigurationMatchMethod, native_enum=False, length=20), nullable=False
    )

    # --- the five coded spec fields that predate the ADR-071 mixin and
    # stay declared directly (same as `catalogue.ModelVariant`). Canonical
    # reference_value.value_code strings. Part of this carrier's spec
    # surface — the drift test checks them alongside the mixin's 38.
    vehicle_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fuel_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body_style: Mapped[str | None] = mapped_column(String(64), nullable=True)
    drivetrain: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transmission: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- the catalogue link (three-column pattern, NO FK) ---------------
    # Not a FK: catalogue variants are provider-sourced and re-synced. A FK
    # would either block a re-sync that removes a row or leave this
    # configuration dangling. The denormalised label + refreshed-at timestamp
    # exist for exactly that — a spec block copied at capture stands on its
    # own even if the variant later moves.
    catalogue_variant_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    catalogue_variant_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    catalogue_variant_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )

    # --- observed at capture, all optional (a configuration is not a vehicle) ---
    vin: Mapped[str | None] = mapped_column(String(17), nullable=True)
    stammnummer: Mapped[str | None] = mapped_column(String(9), nullable=True)
    type_approval_number: Mapped[str | None] = mapped_column(String(6), nullable=True)
    first_registration_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    licence_plate: Mapped[str | None] = mapped_column(String(16), nullable=True)
    mileage_km: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- the vehicle link, set ONLY on a VIN → existing MDM hit (ADR-070) ---
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    vehicle_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    vehicle_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # --- identity tier of the spec block — denormalised strings on this carrier
    # (structural on the variant; free text on a manual configuration) ---
    brand_display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model_group_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    variant_name: Mapped[str | None] = mapped_column(String(160), nullable=True)

    # --- colour (free text or a value_code; OptionenFarben + surcharges are C-E) ---
    exterior_colour: Mapped[str | None] = mapped_column(String(120), nullable=True)
    interior_colour: Mapped[str | None] = mapped_column(String(120), nullable=True)
    exterior_colour_surcharge: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    interior_colour_surcharge: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)

    # Spec-block field names the advisor edited after a catalogue copy
    # (FR-C-03 — "marks that field as overridden, shown as such"). Audit
    # records who/when/what; this list drives the UI marker and re-sync.
    overridden_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    options: Mapped[list["VehicleConfigurationOption"]] = relationship(
        back_populates="configuration",
        cascade="all, delete-orphan",
        order_by="VehicleConfigurationOption.sequence",
    )


class VehicleConfigurationOption(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A selected option on a configuration. C-C ships the table and a
    hand-typed path; priced catalogue options, grouping, packages,
    exclusions, `OptionenFarben` colours, wheels and images are C-E.
    """

    __tablename__ = "vehicle_configuration_option"

    configuration_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicle_configuration.id"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # `OptKey` — null on a hand-typed option.
    variant_option_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    option_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    option_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    is_included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_package: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    configuration: Mapped[VehicleConfiguration] = relationship(back_populates="options")
    equipment_feature_links: Mapped[list["VehicleConfigurationOptionFeature"]] = relationship(
        back_populates="configuration_option", cascade="all, delete-orphan"
    )


class VehicleConfigurationOptionFeature(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """`SuchCode` (045) equipment features on a selected option — the list
    marketplace publishing reads (ADR-062). Mirrors
    `catalogue.VariantOptionEquipmentFeature`. Table name kept short
    (`vehicle_config_option_feature`) to keep every derived index name
    under Postgres' 63-char identifier limit."""

    __tablename__ = "vehicle_config_option_feature"

    configuration_option_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicle_configuration_option.id"), nullable=False, index=True
    )
    feature_value_code: Mapped[str] = mapped_column(String(64), nullable=False)

    configuration_option: Mapped[VehicleConfigurationOption] = relationship(
        back_populates="equipment_feature_links"
    )
