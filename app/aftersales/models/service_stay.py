"""The stay of the car (KAN-813, AS1-10; ADR-213, Decision C; build slice 1,
section 3.4).

A stay is one presence of a car at a Betrieb: the arrival («Fahrzeug ist
da»), later the departure (KAN-817). Presence is a Betrieb's fact, so it is
Aftersales' own — never a field of the shared vehicle record, never a field
of an order: two orders for one car share one stay (decision 61, check 23).

- `service_stay` — the car and the location (each the three-column pattern,
  rule 2), the parking place and key tag, the arrival (who, when, how, the
  mileage, a remark) and the departure, empty until KAN-817.
  **One running stay per car and Betrieb** is the partial unique index
  `uq_service_stay_running_vehicle` — enforced by the database, not only by
  the code.
- `service_order_stay` — which orders a stay holds: an order can have
  several stays (the car leaves and comes back), a stay several orders.
- `service_order_workshop_status` — the projection of the workshop status
  the order list reads (section 3.9). It is worked out by
  `services/workshop_status.py`, its one writer; no endpoint sets it.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base

LABEL_LENGTH = 200


class ArrivalChannel(str, enum.Enum):
    """How the car was received. «At the car on the phone» comes with the slice
    «offer and answer» (section 2)."""

    DESK = "desk"


class WorkshopStatus(str, enum.Enum):
    """Criterion 5, section 3.9: worked out, never set."""

    PLANNED = "planned"
    CAR_HERE = "car_here"
    IN_WORK = "in_work"
    WORK_DONE = "work_done"
    CHECKED = "checked"
    READY_FOR_PICKUP = "ready_for_pickup"
    HANDED_OVER = "handed_over"


class ServiceStay(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "service_stay"
    __table_args__ = (
        # ADR-213: one running stay per car and Betrieb, in the database (check 23).
        Index(
            "uq_service_stay_running_vehicle",
            "tenant_id",
            "vehicle_id",
            unique=True,
            postgresql_where=text("departed_at IS NULL"),
            sqlite_where=text("departed_at IS NULL"),
        ),
    )

    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the vehicle context (VehicleMdm.id). No DB-level FK."
    )
    vehicle_label: Mapped[str] = mapped_column(String(LABEL_LENGTH), nullable=False)
    vehicle_label_refreshed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the platform context (Location.id). No DB-level FK."
    )
    location_label: Mapped[str] = mapped_column(String(LABEL_LENGTH), nullable=False)
    location_label_refreshed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)

    parking_place: Mapped[str | None] = mapped_column(String(100), nullable=True)
    key_tag: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # The arrival (criterion 6: who, when, how).
    arrived_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    arrived_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    arrival_channel: Mapped[ArrivalChannel] = mapped_column(StoredEnum(ArrivalChannel, length=16), nullable=False)
    arrival_mileage: Mapped[int] = mapped_column(Integer, nullable=False)
    # The car's last reading at the arrival (vehicle's), so the reception shows «lower than
    # the last» as it was then (check 22); vehicle flags its own reading.
    previous_mileage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    arrival_remark: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # The departure: KAN-817's one gate writes it.
    departed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    departed_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)


class ServiceOrderStay(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """An order held by a stay (decision 61): created at the arrival for every open order of
    the car, or when a new order is opened for a car that is here."""

    __tablename__ = "service_order_stay"
    __table_args__ = (UniqueConstraint("service_order_id", "stay_id", name="uq_service_order_stay_order_stay"),)

    service_order_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("service_order.id"), nullable=False, index=True
    )
    stay_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("service_stay.id"), nullable=False, index=True)
    joined_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)


class ServiceOrderWorkshopStatus(PrimaryKeyMixin, TenantScopedMixin, Base):
    """The workshop status of an order as the list reads it (criterion 5). One row per
    order; written only by `workshop_status.refresh`."""

    __tablename__ = "service_order_workshop_status"
    __table_args__ = (UniqueConstraint("service_order_id", name="uq_service_order_workshop_status_order"),)

    service_order_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("service_order.id"), nullable=False)
    status: Mapped[WorkshopStatus] = mapped_column(StoredEnum(WorkshopStatus, length=24), nullable=False)
    refreshed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
