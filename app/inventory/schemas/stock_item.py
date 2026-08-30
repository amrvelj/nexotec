"""StockItem schemas (WP-7 PR-1). `vin` is never writable through these —
it is set only by the promotion path (PR-2, ADR-045) and is read-only here
by omission, matching app.vehicle.schemas.vehicle_mdm's own rule that vin
is writable only through VehicleMdmUpdate.
"""

import datetime as dt
import uuid
from decimal import Decimal

from app.core.schemas import CamelModel
from app.inventory.models.stock_item import LifecycleStatus, ReservationState, StockItemCondition


class StockItemCreate(CamelModel):
    vehicle_label: str
    condition: StockItemCondition
    location_id: uuid.UUID | None = None
    odometer_km: int | None = None
    list_price: Decimal | None = None
    effective_price: Decimal | None = None
    first_registration_date: dt.date | None = None
    # Only meaningful for a manually-created, already-in-stock item (e.g.
    # a used car walked onto the lot with a known VIN already assigned in
    # vehicle-mdm) — the ordinary pipeline path never sets this at create
    # time, it arrives later via promotion (PR-2).
    vehicle_id: uuid.UUID | None = None
    vin: str | None = None


class StockItemUpdate(CamelModel):
    vehicle_label: str | None = None
    condition: StockItemCondition | None = None
    location_id: uuid.UUID | None = None
    odometer_km: int | None = None
    list_price: Decimal | None = None
    effective_price: Decimal | None = None
    first_registration_date: dt.date | None = None


class StockItemRead(CamelModel):
    id: uuid.UUID
    stock_number: str
    vehicle_id: uuid.UUID | None
    vin: str | None
    vehicle_label: str
    lifecycle_status: LifecycleStatus
    reservation_state: ReservationState
    condition: StockItemCondition
    location_id: uuid.UUID | None
    odometer_km: int | None
    list_price: Decimal | None
    effective_price: Decimal | None
    first_registration_date: dt.date | None
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime


class StockItemConditionChange(CamelModel):
    condition: StockItemCondition


class StockItemPage(CamelModel):
    items: list[StockItemRead]
    next_cursor: str | None
    total: int
    total_is_estimate: bool
