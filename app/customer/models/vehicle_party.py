"""Owner/Keeper/Driver join table (Swiss addendum decision #7). Lives in the
customer context — domain map: customer owns "the customer-to-vehicle party
link" — not in vehicle, even though it also carries a `vehicle_id` column.

PR-2 (ADR-015, CLAUDE.md rule 2): neither vehicle_id nor customer_id has a
DB-level ForeignKey any more. vehicle_id was a genuine cross-context FK
(customer -> vehicle); customer_id is intra-context after the PR-1 move but
was named explicitly in the PR-2 scope, so it's dropped too rather than
re-litigated. Existence is checked at the application layer (see
app.customer.services.customer, app.vehicle.public.get_vehicle_or_404);
drift is caught by the nightly reconciliation job (app.customer.reconciliation),
not by Postgres.
"""

import datetime as dt
import enum

from sqlalchemy import Enum as SAEnum, UniqueConstraint
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

    vehicle_id: Mapped[GUID] = mapped_column(
        GUID(),
        nullable=False,
        index=True,
        comment="Owned by the vehicle context. No DB-level FK (PR-2, ADR-015) — reconciled nightly.",
    )
    customer_id: Mapped[GUID] = mapped_column(
        GUID(),
        nullable=False,
        index=True,
        comment="Same context (customer) as this table. No DB-level FK (PR-2, ADR-015) — named explicitly in scope.",
    )
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
    #
    # Explicit primaryjoin + foreign(): vehicle_id has no DB-level FK as of
    # PR-2, so SQLAlchemy can no longer infer the join condition on its own.
    # Same eager-load behaviour as before (still driven by joinedload() at
    # the call site) — this only replaces what the dropped FK used to tell it.
    vehicle: Mapped[Vehicle] = relationship(
        primaryjoin="foreign(VehicleParty.vehicle_id) == Vehicle.id",
        viewonly=True,
    )
