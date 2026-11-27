"""The workshop order list's column registry (KAN-848; ADR-179, FR-UI-01,
`.claude/rules/ui-grid.md`: every column sortable server-side). Each field the
order list shows sorts and filters through `app.core.list_query`; the grid
reads which ones sort from `GET /v1/service-orders/grid-schema`.

The registry holds the fields `ServiceOrderSummary` carries. Word columns sort
under `nexotec_text_sort` (ADR-190, applied by `SortField`); the order number
is an identifier (`IDENTIFIER_TEXT` on its column) and keeps byte order.
"""

from app.aftersales.models.service_order import ServiceOrder, ServiceOrderState
from app.core.list_query import ColumnSpec, Registry

_L = "serviceOrders.columns."

SERVICE_ORDER_COLUMNS: Registry = (
    ColumnSpec("orderNumber", ServiceOrder.order_number, "text", _L + "orderNumber", default_visible=True),
    ColumnSpec("vehicleLabel", ServiceOrder.vehicle_label, "text", _L + "vehicle", default_visible=True),
    # The composite «Fahrzeug · Auftraggeber» cell shows the ordering party; its own column is one click away.
    ColumnSpec("contactLabel", ServiceOrder.contact_label, "text", _L + "contact"),
    ColumnSpec(
        "state", ServiceOrder.state, "enum", _L + "state", default_visible=True,
        enum=ServiceOrderState, enum_label_prefix="serviceOrder.state",
    ),
    ColumnSpec("orderDate", ServiceOrder.order_date, "date", _L + "orderDate", default_visible=True),
    ColumnSpec("locationLabel", ServiceOrder.location_label, "text", _L + "location", default_visible=True),
    ColumnSpec("createdAt", ServiceOrder.created_at, "datetime", _L + "createdAt"),
)

DEFAULT_SERVICE_ORDER_SORT = (("createdAt", "desc"),)
