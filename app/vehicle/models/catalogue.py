"""The catalogue layer (WP-5 PR-1): what a model variant *is*, independent of
any physical car and independent of any provider.

The single idea this file rests on: auto-i-dat's FzKey identifies a model
variant such as "Alfa Romeo Giulietta 1.4 TB Progression", not a car.
Thousands of physical vehicles (app.vehicle.models.vehicle_mdm.VehicleMdm,
PR-3) share one ModelVariant row. None of these tables reference a
physical vehicle at all — that link runs the other way, from
VehicleMdm.catalogue_variant_id here.

All tables here are global (no tenant_id) — a model variant is not owned by
a dealer, same reasoning as the shipped Vehicle table's own "a VIN is
decoded manufacturer data" note. No provider code appears in any column
here: PR-2's provider_entity_ref/provider_code_map is the only place a raw
provider code is allowed to exist, and application code reads through it,
never around it.
"""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.types import GUID
from app.db import Base
from app.vehicle.models.spec_block import VehicleSpecBlock


class Brand(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    """Our own brand list, our own spelling and grouping — too
    high-cardinality to fit the reference_list/reference_value shape
    (PRD §Provider abstraction: brand's example row is `alfa-romeo`,
    display "Alfa Romeo"), and unlike reference values a brand has no
    per-language label — a brand name is a proper noun, not translated.
    Versioned like ReferenceValue (PR-8's admin screen edits both) —
    If-Match on every mutation of a versioned entity, per house convention.
    """

    __tablename__ = "vehicle_brand"

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)

    model_groups: Mapped[list["ModelGroup"]] = relationship(back_populates="brand")


class ModelGroup(PrimaryKeyMixin, TimestampMixin, Base):
    """One model line within a brand — e.g. "Giulietta". Groups the
    variants a customer would recognise as "the same car" across trim
    levels and model years.
    """

    __tablename__ = "vehicle_model_group"
    __table_args__ = (UniqueConstraint("brand_id", "name", name="uq_vehicle_model_group_brand_id_name"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("vehicle_brand.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    brand: Mapped[Brand] = relationship(back_populates="model_groups")
    variants: Mapped[list["ModelVariant"]] = relationship(back_populates="model_group")


class ModelVariant(VehicleSpecBlock, PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    """What auto-i-dat's FzKey identifies — e.g. "Giulietta 1.4 TB
    Progression". Descriptive fields here are canonical-taxonomy value_codes
    (reference_value.value_code strings, validated at the service layer via
    app.platform.public.get_reference_value_or_404 — same pattern the
    shipped Vehicle model already uses for its own reference fields), never
    a raw provider code.

    **The full specification block (ADR-071) is mixed in from
    `VehicleSpecBlock`** — the same ~40-field description carried by the
    configuration (C-C) and the host snapshot (C-F). The five coded fields
    below (`vehicle_kind` … `transmission`) predate that block and stay
    declared here: they are already wired through `resolve_provider_code`
    in `catalogue_sync` and moving them would churn a working path for no
    gain. The drift test treats them as part of this carrier's spec surface
    regardless.

    Provider-mirrored, tenant-scoped content (option list, images, colours)
    lives in `catalogue_mirror.py`; the per-model-year price history lives
    in `VariantPrice` below.
    """

    __tablename__ = "vehicle_model_variant"

    model_group_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicle_model_group.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    model_year_from: Mapped[int] = mapped_column(Integer, nullable=False)
    model_year_to: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Reference-data value_codes — nullable because a freshly-seeded variant
    # (or one entered by hand, FR-V-03) may not have every attribute yet.
    vehicle_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fuel_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body_style: Mapped[str | None] = mapped_column(String(64), nullable=True)
    drivetrain: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transmission: Mapped[str | None] = mapped_column(String(64), nullable=True)

    model_group: Mapped[ModelGroup] = relationship(back_populates="variants")
    options: Mapped[list["VariantOption"]] = relationship(back_populates="model_variant")
    prices: Mapped[list["VariantPrice"]] = relationship(
        back_populates="model_variant", cascade="all, delete-orphan"
    )
    type_approval_links: Mapped[list["VariantTypeApproval"]] = relationship(
        back_populates="model_variant", cascade="all, delete-orphan"
    )

    # ADR-071 identity tier — the labelling fields every spec-block carrier
    # must produce. On this (type) carrier they are read-only views over the
    # `model_group → brand` structure; on the configuration / snapshot they
    # are denormalised columns/keys captured at capture time. Callers that
    # read these in bulk should eager-load via
    # `app.vehicle.public.vehicle_mdm_catalogue_loader_option`.
    @property
    def variant_name(self) -> str:
        return self.name

    @property
    def model_group_name(self) -> str | None:
        return self.model_group.name if self.model_group is not None else None

    @property
    def brand_display_name(self) -> str | None:
        group = self.model_group
        if group is None or group.brand is None:
            return None
        return group.brand.display_name


class VariantOption(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A factory option offered on a variant. `description` is stored
    exactly as delivered by the provider — auto-i-dat returns English only
    for options (PRD §Provider abstraction), and translating thousands of
    provider option strings is explicitly out of scope (PRD §Language and
    translations). This is why VariantOption has no label_de/fr/it — it is
    not a reference_value.

    **Tenant-partitioned (WP-6 PR-4, retrofitted)**: ADR-013 — licensed
    provider data is tenant-partitioned, never global, and this table
    holds text delivered under one dealer's own auto-i-dat contract. The
    same variant is cached once per dealer who holds it — deliberate
    duplication, the only licence-safe arrangement (there is no shared
    mirror). The table was empty in every environment before this
    retrofit, so no backfill was needed — just this column and its
    composite unique constraint. `Brand`/`ModelGroup`/`ModelVariant`/
    `TypeApproval` stay global: canonical classification and Swiss
    regulatory fact, never licensed text.
    """

    __tablename__ = "vehicle_variant_option"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "model_variant_id", "option_code", name="uq_vehicle_variant_option_tenant_variant_code"
        ),
    )

    model_variant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicle_model_variant.id"), nullable=False, index=True
    )
    option_code: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # `Gruppe` (CodeGrpNr 046) — a canonical `option_group` value_code
    # string resolved through `provider_code_map`, not a raw provider code
    # and not a FK (reference data is platform-owned). Drives the
    # option-section grouping (FR-C-06). Was a free string before C-A.
    option_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    # C-A — what C-E (KAN-43) needs. All nullable/defaulted so the existing
    # `catalogue_sync` option upsert keeps working unchanged; C-E teaches
    # that upsert (and the adapter DTO) to populate them.
    is_included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """`Inklusiv` — an included option has no price line."""
    is_package: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """`PackCode` — this option *is* a package; its contents come from
    `OptionenPack` (C-E, via `catalogue_option_relation`)."""
    model_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Option availability and relations are scoped **per variant per model
    year** by the provider — the same option pair may be compatible in 2024
    and not in 2025. Nullable until C-0 populates it."""

    model_variant: Mapped[ModelVariant] = relationship(back_populates="options")
    equipment_feature_links: Mapped[list["VariantOptionEquipmentFeature"]] = relationship(
        back_populates="variant_option", cascade="all, delete-orphan"
    )


class VariantOptionEquipmentFeature(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """`SuchCode` (CodeGrpNr 045) — the **70 curated equipment features**
    (Navigation, Klima, Leder, Anhängerkupplung, CarPlay …). Comma-separated
    at the provider, so one option carries several — modelled as child rows
    rather than an array column so the set stays queryable (this is the list
    marketplace publishing reads, ADR-062) and portable across the SQLite
    fast lane. `feature_value_code` is a canonical `equipment_feature`
    value_code resolved through `provider_code_map`, never a raw code.

    Tenant-partitioned like its parent `VariantOption` — it is derived from
    the same licensed provider payload (ADR-013).
    """

    __tablename__ = "vehicle_variant_option_equipment_feature"
    __table_args__ = (
        UniqueConstraint(
            "variant_option_id",
            "feature_value_code",
            name="uq_vehicle_variant_option_equipment_feature_option_feature",
        ),
    )

    variant_option_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicle_variant_option.id"), nullable=False, index=True
    )
    feature_value_code: Mapped[str] = mapped_column(String(64), nullable=False)

    variant_option: Mapped[VariantOption] = relationship(back_populates="equipment_feature_links")


class VariantPrice(PrimaryKeyMixin, TimestampMixin, Base):
    """The new-car list price of a variant **per model year** — `FahrzeugePreise`.

    `Fahrzeuge` returns only `LetzterNP` (the last new price); `FahrzeugeMatch`
    (C-D) will not run without a Neupreis and it must be the price **for that
    car's model year** — a match against the wrong year's price returns a
    plausible wrong car rather than an error.

    **Global**, matching `ModelVariant`: a manufacturer list price per model
    year is factual catalogue data, not the licensed option text that makes
    `VariantOption` tenant-partitioned.

    Whether every year of every variant is seeded up front or filled lazily
    on first identification (PRD Q-C-1) is a C-0 / C-D decision — this shape
    supports both.
    """

    __tablename__ = "vehicle_variant_price"
    __table_args__ = (
        UniqueConstraint("model_variant_id", "model_year", name="uq_vehicle_variant_price_variant_year"),
    )

    model_variant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicle_model_variant.id"), nullable=False, index=True
    )
    model_year: Mapped[int] = mapped_column(Integer, nullable=False)
    new_price: Mapped[Decimal] = mapped_column(DECIMAL(12, 2), nullable=False)
    price_is_net: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """`NettoPreis` — stored as delivered (Q-C-6), converted only for display."""
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="provider")
    """`provider` (`FahrzeugePreise`) · `document` (Fahrzeugausweis / importer
    doc the advisor typed) · `manual`. `FahrzeugeMatch` shows which source a
    typed Neupreis came from (FR-C-02 step 5)."""

    model_variant: Mapped[ModelVariant] = relationship(back_populates="prices")


class TypeApproval(PrimaryKeyMixin, TimestampMixin, Base):
    """A Swiss type-approval (Typenschein) number.

    **A Typenschein is not an identifier.** It is the number an importer
    uses to homologate similar or identical vehicles, so one Typenschein
    normally covers several model variants — and auto-i-dat's `Typenscheine`
    Datenname returns a *list* of them for one FzKey, so one variant also
    carries several Typenscheine. The link to `ModelVariant` is therefore
    many-to-many, through `VariantTypeApproval`. PRD-Vehicles' identifier
    table has always said "Not unique — many cars share one"; the original
    `unique=True` here contradicted it and made FR-C-02's Typenschein
    lookup (1..n → picker) impossible.

    `type_approval_number` is indexed but **not unique**: a lookup by
    Typenschein must always be prepared for 1..n rows. The catalogue-sync
    upsert should reuse an existing row for a known number; the database
    does not enforce it, which also keeps that upsert free of a
    unique-constraint race.

    The same string on the physical `VehicleMdm` record is a separate,
    already-non-unique column copied there purely as a matching key
    (matching waterfall rung 4).
    """

    __tablename__ = "vehicle_type_approval"

    type_approval_number: Mapped[str] = mapped_column(String(6), nullable=False, index=True)

    variant_links: Mapped[list["VariantTypeApproval"]] = relationship(
        back_populates="type_approval", cascade="all, delete-orphan"
    )


class VariantTypeApproval(TimestampMixin, Base):
    """The many-to-many link between a `ModelVariant` and a `TypeApproval`.

    `first_registration_from` lives **here, on the link**, not on
    `TypeApproval`. The date qualifies "this variant is homologated under
    this Typenschein from this first-registration date" — auto-i-dat
    delivers it per (FzKey, Typenschein) pair, and it is exactly what
    disambiguates one Typenschein across the several variants that share
    it. On `TypeApproval` it would force every variant sharing a Typenschein
    to one date — the information loss this m2m change exists to remove
    (same principle as ADR-064).

    Composite PK `(model_variant_id, type_approval_id)`: a variant links to
    a given Typenschein at most once, and the leading column serves the
    forward lookup; the explicit index on `type_approval_id` serves the
    reverse one (FR-C-02 step 4).
    """

    __tablename__ = "vehicle_model_variant_type_approval"

    model_variant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicle_model_variant.id"), primary_key=True
    )
    type_approval_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicle_type_approval.id"), primary_key=True, index=True
    )
    first_registration_from: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    model_variant: Mapped[ModelVariant] = relationship(back_populates="type_approval_links")
    type_approval: Mapped[TypeApproval] = relationship(back_populates="variant_links")
