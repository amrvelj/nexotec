"""The Stock grid's column registry (KAN-461; ADR-060, FR-UI-01, U-10): every
persisted `stock_item` field a column, each sortable and filterable through
`app.core.list_query`, or on `STOCK_ITEM_EXCLUDED_COLUMNS` with the reason
it is not a column. `tests/inventory/test_kan461_stock_grid.py` holds the two lists to
the model: a new column fails until someone decides.

The default-visible set is the grid's documented one (unchanged by
KAN-461; KAN-739 adds «Standort», Anto's ruling of 2026-11-15). Stock's technical columns (fuel, power …) are not here: they need
inventory's own specification snapshot (a separate ticket) — Stock never
reads vehicle-mdm's tables (rule 2)."""

import datetime as dt
from typing import Any

from sqlalchemy import ColumnElement, and_, false, or_

from app.core.base import utcnow
from app.core.list_query import ColumnSpec, Registry
from app.inventory.models.stock_item import (
    AgeingBucket,
    LifecycleStatus,
    ReservationState,
    StockItem,
    StockItemCondition,
)
from app.inventory.services.stock_item import AGEING_AMBER_MAX_DAYS, AGEING_GREEN_MAX_DAYS

_L = "stockList.columns."


def _bucket_clause(bucket: str, now: dt.datetime) -> ColumnElement[bool]:
    """The `in_stock_at` range `compute_ageing_bucket` reads as `bucket`:
    whole days on the lot, ≤ 60 green, ≤ 120 amber, else red."""

    column = StockItem.in_stock_at
    green_from = now - dt.timedelta(days=AGEING_GREEN_MAX_DAYS + 1)
    amber_from = now - dt.timedelta(days=AGEING_AMBER_MAX_DAYS + 1)
    if bucket == AgeingBucket.GREEN.value:
        return column > green_from
    if bucket == AgeingBucket.AMBER.value:
        return and_(column > amber_from, column <= green_from)
    return column <= amber_from


def _ageing_bucket_filter(operator: str, value: Any) -> ColumnElement[bool]:
    now = utcnow()
    if operator == "in":
        return or_(false(), *(_bucket_clause(code, now) for code in value))
    clause = _bucket_clause(value, now)
    if operator == "is":
        return clause
    # «ist nicht»: a pipeline car (no bucket) is kept (ADR-102).
    return or_(StockItem.in_stock_at.is_(None), ~clause)


STOCK_ITEM_COLUMNS: Registry = (
    # The documented default-visible set (StockListPage, ADR-060).
    ColumnSpec("stockNumber", StockItem.stock_number, "text", _L + "stockNumber", default_visible=True),
    ColumnSpec("vehicleLabel", StockItem.vehicle_label, "text", _L + "vehicle", default_visible=True),
    ColumnSpec(
        "condition", StockItem.condition, "enum", _L + "condition", default_visible=True,
        enum=StockItemCondition, enum_label_prefix="stockEnums.condition",
    ),
    ColumnSpec(
        "lifecycleStatus", StockItem.lifecycle_status, "enum", _L + "lifecycleStatus", default_visible=True,
        enum=LifecycleStatus, enum_label_prefix="stockEnums.lifecycleStatus",
    ),
    ColumnSpec(
        "reservationState", StockItem.reservation_state, "enum", _L + "reservationState", default_visible=True,
        enum=ReservationState, enum_label_prefix="stockEnums.reservationState",
    ),
    ColumnSpec("odometerKm", StockItem.odometer_km, "number", _L + "odometerKm", default_visible=True),
    ColumnSpec("effectivePrice", StockItem.effective_price, "money", _L + "effectivePrice", default_visible=True),
    ColumnSpec("updatedAt", StockItem.updated_at, "datetime", _L + "changed", default_visible=True),
    # KAN-739 (Anto, 2026-11-15): «Standort» joins the documented default-visible
    # set — the location's name, rule 2's copy beside `location_id`.
    ColumnSpec("locationLabel", StockItem.location_label, "text", _L + "location", default_visible=True),
    # Every other persisted field, one click away.
    ColumnSpec("vin", StockItem.vin, "text", _L + "vin"),
    ColumnSpec("listPrice", StockItem.list_price, "money", _L + "listPrice"),
    ColumnSpec(
        "ageingBucket", None, "enum", _L + "ageingBucket",
        enum=AgeingBucket, enum_label_prefix="stockEnums.ageingBucket",
        # Derived on read from in_stock_at (never stored): green first means
        # the most recent arrival first.
        sort_column=StockItem.in_stock_at, sort_inverted=True, filter_builder=_ageing_bucket_filter,
    ),
    ColumnSpec("basePrice", StockItem.base_price, "money", _L + "basePrice"),
    ColumnSpec("purchasePrice", StockItem.purchase_price, "money", _L + "purchasePrice"),
    ColumnSpec("landedCost", StockItem.landed_cost, "money", _L + "landedCost"),
    ColumnSpec(
        "notionalInputTaxAmount", StockItem.notional_input_tax_amount, "money", _L + "notionalInputTaxAmount"
    ),
    ColumnSpec("isInvoiceable", StockItem.is_invoiceable, "boolean", _L + "isInvoiceable"),
    ColumnSpec("supplierName", StockItem.supplier_name, "text", _L + "supplierName"),
    ColumnSpec("purchaseDate", StockItem.purchase_date, "date", _L + "purchaseDate"),
    ColumnSpec("orderDate", StockItem.order_date, "date", _L + "orderDate"),
    ColumnSpec("expectedDelivery", StockItem.expected_delivery, "date", _L + "expectedDelivery"),
    ColumnSpec("pipelineRef", StockItem.pipeline_ref, "text", _L + "pipelineRef"),
    ColumnSpec("configurationLabel", StockItem.configuration_label, "text", _L + "configuration"),
    # KAN-461: the persisted fields the grid did not offer yet.
    ColumnSpec("exteriorColour", StockItem.exterior_colour, "text", _L + "exteriorColour"),
    ColumnSpec("bodyStyle", StockItem.body_style, "text", _L + "bodyStyle"),
    ColumnSpec("firstRegistrationDate", StockItem.first_registration_date, "date", _L + "firstRegistrationDate"),
    ColumnSpec("inStockAt", StockItem.in_stock_at, "datetime", _L + "inStockAt"),
    ColumnSpec("tradeInVin", StockItem.trade_in_vin, "text", _L + "tradeInVin"),
    ColumnSpec("tradeInSignedAt", StockItem.trade_in_signed_at, "datetime", _L + "tradeInSignedAt"),
    ColumnSpec("supplierIsVatRegistered", StockItem.supplier_is_vat_registered, "boolean", _L + "supplierIsVatRegistered"),
    ColumnSpec("purchaseInvoiceRef", StockItem.purchase_invoice_ref, "text", _L + "purchaseInvoiceRef"),
    ColumnSpec(
        "notionalInputTaxApplicable", StockItem.notional_input_tax_applicable, "boolean",
        _L + "notionalInputTaxApplicable",
    ),
    ColumnSpec("notionalInputTaxRate", StockItem.notional_input_tax_rate, "number", _L + "notionalInputTaxRate"),
    ColumnSpec(
        "notionalInputTaxOverridden", StockItem.notional_input_tax_overridden, "boolean",
        _L + "notionalInputTaxOverridden",
    ),
    ColumnSpec("valuationRefAmount", StockItem.valuation_ref_amount, "money", _L + "valuationRefAmount"),
    ColumnSpec("valuationRefValuedAt", StockItem.valuation_ref_valued_at, "datetime", _L + "valuationRefValuedAt"),
    ColumnSpec("valuationRefSource", StockItem.valuation_ref_source, "text", _L + "valuationRefSource"),
    ColumnSpec("createdAt", StockItem.created_at, "datetime", _L + "createdAt"),
)

# Each persisted column that is not a grid column, and why (ADR-060: a
# stored field that cannot be put on screen is a defect — these are ids,
# bookkeeping or a scope, not a business fact of their own). FR-UI-01's PRD justification: PRD-Stock's delivery
# line for KAN-461 records this list.
STOCK_ITEM_EXCLUDED_COLUMNS: dict[str, str] = {
    "id": "the row's identity; the grid opens the car by it",
    "tenant_id": "the list's scope (rule 7), the same for every row",
    "version": "If-Match bookkeeping, no business fact",
    "created_by": "a user id with no label column; the History tab names it",
    "updated_by": "a user id with no label column; the History tab names it",
    "vehicle_id": "the vehicle-mdm record's id; the car is shown by its label and VIN",
    "configuration_id": "the configuration's id; shown by `configurationLabel`",
    "configuration_label_refreshed_at": "label-refresh bookkeeping (the three-column pattern)",
    "reserved_by_contract_id": "Sales' contract id; `reservationState` shows the reservation",
    "active_reservation_id": "the reservation row's id; `reservationState` shows the reservation",
    "trade_in_customer_id": "a customer id relayed from Sales with no label on the item",
    "valuation_ref_id": "the valuation's id; its amount, source and date are columns",
    "left_stock_at": "always empty in this list: a car that left stock is not in it (FR-I-12)",
    "location_id": "the location's id; shown by `locationLabel` (KAN-739)",
    "location_label_refreshed_at": "label-refresh bookkeeping (the three-column pattern)",
}

DEFAULT_STOCK_ITEM_SORT: tuple[tuple[str, str], ...] = (("updatedAt", "desc"),)
