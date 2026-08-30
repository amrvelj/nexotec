"""ADR-055: the group-readable stock projection is its own enumerated
shape, never the tenant grid's schema with fields hidden at
serialization time. effectivePrice, discounts/promotions, acquisition
(landedCost, notionalInputTax*, purchasePrice) and Wagenbuch data are
absent BY CONSTRUCTION here — a field this schema never declares can't
leak through a forgotten `exclude` list the way a shared schema's could.
listPrice stands in for effectivePrice; dealershipId/dealershipLabel are
first-class, filterable/sortable dimensions unique to this projection.
"""

import datetime as dt
import uuid
from decimal import Decimal

from app.core.schemas import CamelModel
from app.inventory.models.stock_item import LifecycleStatus, ReservationState, StockItemCondition


class StockItemGroupRead(CamelModel):
    id: uuid.UUID
    dealership_id: uuid.UUID
    dealership_label: str
    stock_number: str
    vin: str | None
    vehicle_label: str
    lifecycle_status: LifecycleStatus
    reservation_state: ReservationState
    condition: StockItemCondition
    odometer_km: int | None
    list_price: Decimal | None
    first_registration_date: dt.date | None
    updated_at: dt.datetime


class StockItemGroupPage(CamelModel):
    items: list[StockItemGroupRead]
