"""Valuation (WP-8 PR-5) — the 11th bounded context (ADR-066/ADR-048 as
amended, FR-V-09/FR-V-17). "A dated commercial opinion, not a vehicle
fact" (PRD-Vehicles' own module-decomposition table) — tenant-private even
within a group (ADR-029), unlike vehicle identity, which is global. This
is why it is not filed under `app.vehicle`.

Creatable with no customer, no offer and no vehicle in the register
(confirmed live, verbatim, on the reference prototype's own create
dialog: "Ohne Kunde, ohne Offerte, ohne Fahrzeug im Bestand"). `vehicle_id`
is therefore nullable, and — when null — the vehicle's own facts
(make/model/trim/plate/VIN/mileage/first registration) are captured
directly on this row, since there is no vehicle-mdm record to hold them.
When a VIN IS given, `services/valuation.py::create_valuation` calls
`app.vehicle.public.create_or_get_vehicle_mdm` in the same step (confirmed
live: "Ist das Fahrzeug nicht erfasst, wird es mit der Bewertung angelegt
— ein Schritt, nicht zwei") and vehicle_id is set.

Lifecycle draft -> valid -> expired -> used is evaluated ON READ from
`is_draft`/`valid_until`/`used_at` — never stored, never repaired by a
nightly job (services/valuation.py::derive_status). A vehicle carries a
LIST of valuations: the newest is current, older ones stay readable as
superseded and are never edited or deleted — `supersedes_valuation_id`
records the lineage when "Neu bewerten" replaces an existing one.
"""

import datetime as dt
import enum
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, Boolean, Date, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.types import GUID, UTCDateTime
from app.db import Base


class ValuationSource(str, enum.Enum):
    AUTO_I_DAT = "auto_i_dat"
    MANUAL = "manual"


class ValuationNumberSequence(Base):
    """Per-tenant row-lock allocator for `B-000001`-style numbers — same
    idiom as app.inventory's StockNumberSequence. Per-tenant, not
    per-group or global, matching ADR-029: a valuation is private to the
    legal entity, not shared group-wide the way vehicle identity is.
    """

    __tablename__ = "valuation_number_sequence"

    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Valuation(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "valuation"

    valuation_number: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # Null = no vehicle in the register yet (confirmed live). When set,
    # the make/model/trim/vin fields below stay populated too — denormalized
    # for display without a cross-context join, same posture as
    # app.inventory.StockItem.vehicle_label.
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, index=True, comment="Owned by the vehicle context (VehicleMdm.id). No DB-level FK."
    )
    vehicle_make: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vehicle_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vehicle_trim: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vehicle_plate: Mapped[str | None] = mapped_column(String(16), nullable=True)
    vehicle_vin: Mapped[str | None] = mapped_column(String(17), nullable=True, index=True)
    vehicle_first_registration: Mapped[dt.date | None] = mapped_column(Date(), nullable=True)
    mileage: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Null = "Ohne Kunde" (confirmed live filter chip — the standalone
    # application's own unattached case, FR-V-17).
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, index=True, comment="Owned by the customer context. No DB-level FK."
    )
    customer_label: Mapped[str | None] = mapped_column(String(200), nullable=True)

    source: Mapped[ValuationSource] = mapped_column(
        SAEnum(ValuationSource, native_enum=False, length=16), nullable=False
    )
    provider_value: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    # IHR EINTAUSCHANGEBOT (confirmed live) — the final trade-in offer.
    # Deliberately separate from provider_value minus deductions: "das
    # Eintauschangebot darf vom Nettowert abweichen — das ist die
    # Verhandlung" (confirmed live create-dialog hint).
    final_offer: Mapped[Decimal] = mapped_column(DECIMAL(12, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    valid_from: Mapped[dt.date] = mapped_column(Date(), nullable=False)
    valid_until: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)

    # The only stored lifecycle fact besides valid_until/used_at — everything
    # else (valid/expired) is derived on read. A draft is never counted as
    # "valid" regardless of its own valid_until.
    is_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Set once, by app.valuation.public.mark_valuation_used (Sales calls
    # this at contract confirmation, PR-6) — "used" is terminal, an offer
    # a contract has already consumed, never re-offered.
    used_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    supersedes_valuation_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)


class ValuationDeduction(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """Append-alongside-the-valuation child rows — a valuation is never
    edited after creation (ADR-066), so these are written once, at
    create_valuation time, same-transaction, never updated individually.
    """

    __tablename__ = "valuation_deduction"

    valuation_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[Decimal] = mapped_column(DECIMAL(12, 2), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
