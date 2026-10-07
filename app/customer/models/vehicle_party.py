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

KAN-84 (CLAUDE.md rule 2): the vehicle's display fields are denormalised
onto this row — the three-column pattern, with six label columns in place
of one — instead of the viewonly relationship() to VehicleMdm this used to
join through. Written whenever a party row is opened or repointed (through
app.vehicle.public.get_vehicle_summaries) and re-read nightly by
app.customer.services.customer.refresh_vehicle_party_labels, so a later
catalogue match or VIN correction shows within a day.
tests/architecture/test_no_cross_context_mapping.py keeps it that way.
"""

import dataclasses
import datetime as dt
import enum
import uuid

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, utcnow
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base


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
        StoredEnum(VehiclePartyRole, length=16), nullable=False
    )
    effective_from: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    effective_to: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # KAN-84 — the vehicle's label, denormalised (rule 2). Owned by the
    # vehicle context; copied from app.vehicle.public.get_vehicle_summaries.
    # Nullable only because rows written before KAN-84 carry none until the
    # nightly refresh reaches them; the read path fills those in memory.
    vehicle_vin: Mapped[str | None] = mapped_column(
        String(17), nullable=True, comment="Label from the vehicle context (vehicle_mdm.vin). KAN-84."
    )
    vehicle_number: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="Label from the vehicle context (vehicle_mdm.vehicle_number). KAN-84."
    )
    vehicle_make: Mapped[str | None] = mapped_column(
        String(120), nullable=True, comment="Label from the vehicle context (catalogue brand). KAN-84."
    )
    vehicle_model: Mapped[str | None] = mapped_column(
        String(120), nullable=True, comment="Label from the vehicle context (catalogue model group). KAN-84."
    )
    vehicle_model_year: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="Label from the vehicle context (first registration year). KAN-84."
    )
    vehicle_trim: Mapped[str | None] = mapped_column(
        String(160), nullable=True, comment="Label from the vehicle context (catalogue variant name). KAN-84."
    )
    vehicle_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(
        UTCDateTime(), nullable=True, comment="When the vehicle_* labels were last read from the vehicle context."
    )

    @property
    def vehicle(self) -> "VehiclePartyVehicleLabel":
        """The label columns in the shape VehiclePartySummary reads
        (`CustomerVehicleRead.vehicle`) — no join, no other context's row.
        A row with no stored label yet may carry a read-only fill (see
        app.customer.services.customer._fill_unlabelled_vehicle_parties_for_read),
        held in a plain instance attribute, never in ORM state."""

        read_fill = self.__dict__.get("_vehicle_label_read_fill")
        if read_fill is not None:
            return read_fill
        return VehiclePartyVehicleLabel(
            id=self.vehicle_id,
            vin=self.vehicle_vin,
            vehicle_number=self.vehicle_number,
            make=self.vehicle_make,
            model=self.vehicle_model,
            model_year=self.vehicle_model_year,
            trim=self.vehicle_trim,
        )


@dataclasses.dataclass(frozen=True)
class VehiclePartyVehicleLabel:
    id: uuid.UUID
    vin: str | None
    vehicle_number: str | None
    make: str | None
    model: str | None
    model_year: int | None
    trim: str | None
