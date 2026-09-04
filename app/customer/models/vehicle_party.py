"""Owner/Keeper/Driver join table (Swiss addendum decision #7). Lives in the
customer context — domain map: customer owns "the customer-to-vehicle party
link" — not in vehicle, even though it also carries a `vehicle_id` column.

PR-2 (ADR-015, CLAUDE.md rule 2): neither vehicle_id nor customer_id has a
DB-level ForeignKey any more. vehicle_id was a genuine cross-context FK
(customer -> vehicle); customer_id is intra-context after the PR-1 move but
was named explicitly in the PR-2 scope, so it's dropped too rather than
re-litigated. Existence is checked at the application layer (see
app.customer.services.customer, app.vehicle.public.get_vehicle_mdm_or_404);
drift is caught by the nightly reconciliation job (app.customer.reconciliation),
not by Postgres.

KAN-31: vehicle_id resolves against `vehicle_mdm` (WP-5's three-layer
model), never the legacy `vehicle` table this replaces — that table's
writes are frozen (ADR-021) and its rows are provenance-only going
forward. A row created before this fix could in principle still point at
a legacy vehicle id; app.vehicle.reconciliation::
count_unrepointed_legacy_vehicle_party_references (WP-5 PR-7) is the
health check for exactly that, gating the old table's eventual
retirement — it counts, it does not repoint. As of this fix there are
zero VehicleParty rows in any environment (the customer-side create path
404'd on every real attempt before now), so there is nothing to migrate;
see scripts/repoint_legacy_vehicle_party_references.py for the
defensive, idempotent repoint-or-report pass this ticket's own review
asked for, kept as a safety net rather than a live migration.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import PrimaryKeyMixin, TimestampMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base
from app.vehicle.public import VehicleMdm


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

    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        nullable=False,
        index=True,
        comment="Owned by the vehicle context. No DB-level FK (PR-2, ADR-015) — reconciled nightly.",
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
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
    # on VehicleMdm: nothing needs "all parties for this vehicle" yet, and
    # adding an unused collection relationship is guessing at a shape no
    # endpoint asks for.
    #
    # KAN-31: repointed from the legacy Vehicle table to VehicleMdm — see
    # the module docstring. Explicit primaryjoin + foreign(): vehicle_id
    # has no DB-level FK as of PR-2, so SQLAlchemy can no longer infer the
    # join condition on its own. Same eager-load behaviour as before
    # (still driven by joinedload() at the call site) — this only
    # replaces what the dropped FK used to tell it.
    vehicle: Mapped[VehicleMdm] = relationship(
        primaryjoin="foreign(VehicleParty.vehicle_id) == VehicleMdm.id",
        viewonly=True,
    )
