"""The physical vehicle (WP-5 PR-3): "what is the car with VIN
ZAR94000007123456?" — reduced to identity only. Deliberately a NEW file,
not an edit to the shipped app/vehicle/models/vehicle.py — PR-7 needs that
old model to keep compiling, read-only, until cutover, and a one-way
migration is much easier to reason about between two distinct classes than
between two states of one class.

Everything the shipped table got wrong on purpose gets fixed here:
- vin no longer sits next to make/model/trim — those belong to
  ModelVariant (PR-1); catalogue_variant_id is the only link, and it's
  nullable (an unverified, off-catalogue vehicle has none, FR-V-03).
- vehicle_status is lifecycle ONLY (active/exported/scrapped/stolen) —
  never stock state. The exact conflation ADR-021/ADR-040 exist to undo.
- condition does not live here at all (ADR-040) — it's inventory's.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.types import GUID
from app.db import Base
from app.vehicle.models.catalogue import ModelVariant


class CatalogueMatchStatus(str, enum.Enum):
    MATCHED = "matched"
    UNVERIFIED = "unverified"


class VehicleStatus(str, enum.Enum):
    """Lifecycle only — never stock state (ADR-040). Labels for these four
    codes are also seeded as reference values (PR-1's vehicle_status list)
    purely so PR-8's admin screen is the one place translations are
    maintained; the column itself stays this hardcoded enum.
    """

    ACTIVE = "active"
    EXPORTED = "exported"
    SCRAPPED = "scrapped"
    STOLEN = "stolen"


class VehicleNumberSequence(Base):
    """Single global row allocating vehicle_number (`F-000001`). Global,
    not per-group like CustomerNumberSequence (app.customer.models.customer)
    — a physical vehicle is a global fact (ADR-022), so unlike a customer
    it must never receive a different number in two different groups' eyes.
    Same row-lock allocator, same "gaps are harmless, reuse is not"
    reasoning, just one row instead of one per group.
    """

    __tablename__ = "vehicle_number_sequence"

    # A fixed literal key rather than a boolean/no-PK singleton: keeps the
    # same db.get(Model, pk, with_for_update=True) idiom the customer
    # allocator already uses, with no special-cased lookup.
    singleton_key: Mapped[str] = mapped_column(String(16), primary_key=True, default="GLOBAL")
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class VehicleMdm(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    """The consolidated, authoritative record for one physical vehicle,
    independent of catalogue provider and independent of which tenant last
    touched it. No tenant_id — same "a VIN is decoded manufacturer data"
    reasoning as the table this replaces.
    """

    __tablename__ = "vehicle_mdm"

    vin: Mapped[str] = mapped_column(String(17), nullable=False, unique=True, index=True)
    vehicle_number: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    stammnummer: Mapped[str | None] = mapped_column(String(9), nullable=True, unique=True, index=True)
    type_approval_number: Mapped[str | None] = mapped_column(String(6), nullable=True, index=True)
    first_registration_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    catalogue_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("vehicle_model_variant.id"), nullable=True
    )
    catalogue_match_status: Mapped[CatalogueMatchStatus] = mapped_column(
        SAEnum(CatalogueMatchStatus, native_enum=False, length=16),
        nullable=False,
        default=CatalogueMatchStatus.UNVERIFIED,
    )

    vehicle_status: Mapped[VehicleStatus] = mapped_column(
        SAEnum(VehicleStatus, native_enum=False, length=16), nullable=False, default=VehicleStatus.ACTIVE
    )

    # Provenance of a one-way PR-7 migration — permanently retained per
    # FR-V-04's own "MDM permanently stores pipeline_vehicle_id as
    # provenance" precedent, applied here to the OLD vehicle table's id
    # instead. Never used for lookups; purely an audit trail.
    migrated_from_legacy_vehicle_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)

    # PR-6 merge (FR-V-12): one-way, no unmerge. Set exactly once, on the
    # duplicate, when it loses a merge — never cleared, never repointed
    # again. A non-null value here means "this id is retired; resolve
    # merged_into_vehicle_id instead," everywhere this id is still held.
    merged_into_vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("vehicle_mdm.id"), nullable=True, index=True
    )

    catalogue_variant: Mapped[ModelVariant | None] = relationship()
