"""Plates (WP-5 PR-4) — READ ADR-039. Two entirely different data classes
sharing the word "plate" in English, which is exactly why conflating them
in one table is the mistake this file exists to avoid.

Kontrollschild (VehiclePlate): a time-bounded fact kept in the register of
the DEALER GROUP that recorded it (ADR-039 as amended by KAN-467, Anto
2026-10-31: "it's on a group level"), attributed to the Betrieb that entered
it. Two groups may each hold ZH 123456 on different cars (one of them is
stale); neither ever sees the other's plate-to-car link. Inside one group a
plate is current on one car at a time — recording it on another car moves it
once the user confirms (the old assignment ends the day before) — or on the
two cars of a Wechselschild.

Händlerschild/U-Schild (DealerPlate + DealerPlateAssignment): a TENANT-
SCOPED asset of the dealership itself, moved between vehicles daily for
test drives and transfers. Nothing about it is a fact about the car.

A Wechselschild — one Kontrollschild legally shared by two vehicles, only
one driven at a time — is two current VehiclePlate rows sharing a
plate_group_id, and their overlap is EXPECTED. Two current rows of one
group for the same plate WITHOUT a shared plate_group_id cannot be recorded
(`services/plate.py::record_plate` refuses or moves), so the cross-group
data-quality queue ADR-039 first ruled (`vehicle_plate_conflict`) is gone.
"""

import datetime as dt
import uuid

from sqlalchemy import Boolean, Date, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base
from app.vehicle.models.vehicle_mdm import VehicleMdm


class VehiclePlate(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    """Kontrollschild, in one dealer group's register (`group_id`, KAN-467).
    Not TenantScopedMixin: every Betrieb of the group reads and writes the
    same register; `recording_tenant_id` records which one entered the
    assignment (provenance, as on the odometer log). Versioned since KAN-467:
    ending an assignment takes If-Match on the row.
    """

    __tablename__ = "vehicle_plate"
    # The lookup index (KAN-467): every read names the group, then the
    # exact plate + canton — never a scan of the register.
    __table_args__ = (Index("ix_vehicle_plate_group_plate_canton", "group_id", "plate", "canton"),)

    group_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        nullable=False,
        comment="Owned by the platform context (DealerGroup) — the register this assignment is kept in. "
        "No DB-level FK.",
    )

    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("vehicle_mdm.id"), nullable=False, index=True)
    plate: Mapped[str] = mapped_column(String(16), nullable=False)
    canton: Mapped[str] = mapped_column(String(2), nullable=False)
    valid_from: Mapped[dt.date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    is_interchangeable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    plate_group_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    recording_tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the platform context (Dealership). No DB-level FK — reconciled nightly."
    )

    vehicle: Mapped[VehicleMdm] = relationship()


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
