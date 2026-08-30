"""StockItem schemas (WP-7 PR-1). `vin` is never writable through these —
it is set only by the promotion path (PR-2, ADR-045) and is read-only here
by omission, matching app.vehicle.schemas.vehicle_mdm's own rule that vin
is writable only through VehicleMdmUpdate.
"""

import datetime as dt
import uuid
from decimal import Decimal

from app.core.schemas import CamelModel
from app.inventory.models.stock_item import AgeingBucket, LifecycleStatus, ReservationState, StockItemCondition


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
    pipeline_ref: str | None
    order_date: dt.date | None
    expected_delivery: dt.date | None
    in_stock_at: dt.datetime | None
    supplier_name: str | None
    supplier_is_vat_registered: bool | None
    purchase_date: dt.date | None
    purchase_price: Decimal | None
    purchase_invoice_ref: str | None
    landed_cost: Decimal | None
    notional_input_tax_applicable: bool | None
    notional_input_tax_rate: Decimal | None
    notional_input_tax_amount: Decimal | None
    notional_input_tax_overridden: bool
    is_invoiceable: bool
    left_stock_at: dt.datetime | None
    # WP-7 PR-9 (FR-I-22) — the base price a factory-option list adds to.
    base_price: Decimal | None
    # WP-7 PR-9 (ADR-066/ADR-048) — a denormalized pointer only; the
    # valuation module (WP-8) is the single writer of the real record.
    valuation_ref_id: uuid.UUID | None
    valuation_ref_amount: Decimal | None
    valuation_ref_valued_at: dt.datetime | None
    valuation_ref_source: str | None
    # Computed on read (PR-7), never stored — see
    # services/stock_item.py::compute_ageing_bucket. None until the model
    # is post-processed by the API layer; model_validate alone leaves it
    # at this default, same pattern as customer's own computed
    # projections (customer/api/customers.py::_customer_read).
    ageing_bucket: AgeingBucket | None = None
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
