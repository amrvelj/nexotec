"""Plates (WP-5 PR-4) — READ ADR-039. Two entirely different data classes
sharing the word "plate" in English, which is exactly why conflating them
in one table is the mistake this file exists to avoid.

Kontrollschild (VehiclePlate): a GLOBAL fact, time-bounded, attributed to
whichever tenant recorded it — a garage in Basel and a garage in Lugano can
both correctly know the same car wore "ZH 12345" from March to June.

Händlerschild/U-Schild (DealerPlate + DealerPlateAssignment): a TENANT-
SCOPED asset of the dealership itself, moved between vehicles daily for
test drives and transfers. Nothing about it is a fact about the car.

A Wechselschild — one Kontrollschild legally shared by two vehicles, only
one driven at a time — is two current VehiclePlate rows sharing a
plate_group_id, and their overlap is EXPECTED, never a conflict. Two
current rows for the same plate WITHOUT a shared plate_group_id is the
genuine error case (VehiclePlateConflict, this file's admin-queue sibling
to app.vehicle.models.provider.MappingGap).
"""

import datetime as dt
import uuid

from sqlalchemy import Boolean, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base
from app.vehicle.models.vehicle_mdm import VehicleMdm


class VehiclePlate(PrimaryKeyMixin, TimestampMixin, Base):
    """Kontrollschild. Global — no TenantScopedMixin — but
    `recording_tenant_id` still records who observed/entered this
    assignment, same "global fact, tenant-attributed" shape as the odometer
    log (PR-5) and for the same reason: mileage/plate-sighting is a fact
    about the car, but knowing who recorded it matters for provenance.
    """

    __tablename__ = "vehicle_plate"

    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("vehicle_mdm.id"), nullable=False, index=True)
    plate: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    canton: Mapped[str] = mapped_column(String(2), nullable=False)
    valid_from: Mapped[dt.date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    is_interchangeable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    plate_group_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    recording_tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the platform context (Dealership). No DB-level FK — reconciled nightly."
    )

    vehicle: Mapped[VehicleMdm] = relationship()


class VehiclePlateConflict(PrimaryKeyMixin, Base):
    """Admin data-quality queue entry (PR-8) for two CURRENT assignments of
    one Kontrollschild to different VINs that do NOT share a
    plate_group_id — a genuine data error, never resolved by letting each
    tenant keep a private version. A legitimate Wechselschild (shared
    plate_group_id) never creates a row here, by construction — see
    app.vehicle.services.plate.record_plate_assignment.
    """

    __tablename__ = "vehicle_plate_conflict"

    plate: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    canton: Mapped[str] = mapped_column(String(2), nullable=False)
    first_plate_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("vehicle_plate.id"), nullable=False)
    second_plate_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("vehicle_plate.id"), nullable=False)
    detected_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class DealerPlate(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """Händlerschild/U-Schild — an asset the dealership owns outright, not
    a fact about any car. `plate` here is the dealer-plate number itself
    (e.g. "ZH U 1234"), not a Kontrollschild string.
    """

    __tablename__ = "vehicle_dealer_plate"

    plate: Mapped[str] = mapped_column(String(16), nullable=False)
    canton: Mapped[str] = mapped_column(String(2), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DealerPlateAssignment(PrimaryKeyMixin, TimestampMixin, Base):
    """Which vehicle currently (or previously) carried a dealer plate.
    Kept as history via valid_from/valid_to (open question Q-10 in the PRD
    — "do dealers need assignment history, or only current" — resolved
    here toward keeping it: the marginal cost of one nullable column is
    small next to permanently losing "which car had U-12345 last Tuesday",
    and every other time-bounded table in this module already keeps
    history on the same principle).
    """

    __tablename__ = "vehicle_dealer_plate_assignment"

    dealer_plate_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicle_dealer_plate.id"), nullable=False, index=True
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("vehicle_mdm.id"), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        nullable=False,
        index=True,
        comment="Denormalized from DealerPlate.tenant_id at insert time — same reasoning as CustomerPhone.group_id.",
    )
    valid_from: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    valid_to: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    dealer_plate: Mapped[DealerPlate] = relationship()
