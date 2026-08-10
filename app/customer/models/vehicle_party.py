"""Owner/Keeper/Driver join table (Swiss addendum decision #7). Lives in the
customer context — domain map: customer owns "the customer-to-vehicle party
link" — not in vehicle, even though it also carries a `vehicle_id` FK.

`vehicle_id`'s ForeignKey("vehicle.id") is a cross-context reference (one of
the nine flagged in CLAUDE.md for PR-2, which converts it to a plain GUID
column). Left as a real FK here for zero behaviour change; PR-2 removes it.
"""

import datetime as dt
import enum

from sqlalchemy import Enum as SAEnum, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import PrimaryKeyMixin, TimestampMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base
from app.vehicle.public import Vehicle


class VehiclePartyRole(str, enum.Enum):
    OWNER = "owner"  # Eigentümer
    KEEPER = "keeper"  # Halter
    DRIVER = "driver"  # Fahrzeugführer — operational only, no registry standing


class VehicleParty(PrimaryKeyMixin, TimestampMixin, Base):
    """Owner/Keeper/Driver join table (Swiss addendum decision #7).

    API endpoints live under /v1/customers/{id}/vehicles (Customer PRD D-12,
    FR-10) rather than under /vehicles — the 360 view's Vehicles tab is the
    consumer, and the customer is always the anchor of the relationship
    being edited. `role` is immutable once created (like Customer.
    customer_type): a role changing hands is a new row with its own
    effective_from, not an edit of the old one — that's what "without
    losing history" (FR-10) means in practice.
    """

    __tablename__ = "vehicle_party"
    __table_args__ = (
        UniqueConstraint("vehicle_id", "customer_id", "role", "effective_from", name="uq_vehicle_party_scope"),
    )

    vehicle_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("vehicle.id"), nullable=False, index=True)
    # FK tightened on integration/mdm-shell (2026-08-06) now that Customer
    # (issue #4) is actually present — was a bare UUID forward-reference on
    # the issue-5-vehicle branch, which never had Customer in its history.
    customer_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("customer.id"), nullable=False, index=True)
    role: Mapped[VehiclePartyRole] = mapped_column(
        SAEnum(VehiclePartyRole, native_enum=False, length=16), nullable=False
    )
    effective_from: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    effective_to: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # Read-only convenience for the customer-vehicle list response (D-12) —
    # the 360 view needs VIN/make/model, not just the FK. No back-populates
    # on Vehicle: nothing needs "all parties for this vehicle" yet, and
    # adding an unused collection relationship is guessing at a shape no
    # endpoint asks for.
    vehicle: Mapped[Vehicle] = relationship()
