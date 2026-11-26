"""The only surface other contexts may import from aftersales. Import-linter's
contract allows `app.<other-context>` to import `app.aftersales.public`, never
`app.aftersales.models` / `services` / `api` / `schemas` / `reconciliation`.

It hands out functions, enums and plain values, never a mapped class
(ADR-162). Today: the exporter the composition root registers (KAN-218),
the order's states, and the names of the facts the order publishes
(KAN-810) — `aftersales.service_order.created` and `.cancelled`, one payload
shape (ADR-202): `serviceOrderId`, `orderNumber`, `state`, `locationId`,
`orderDate`, `contactId`, `invoiceRecipientContactId`, `driverContactId`,
`vehicleId`, `cancelledAt`, `cancelledBy`, `cancelReason`. Nothing consumes
them yet. KAN-837: the nightly label refresh of the orders
(`refresh_order_labels`, the daily job `aftersales.order_labels`) and its
gauge (`oldest_order_label_age_seconds`), for the worker.
"""

from app.aftersales.models.service_order import ServiceOrderState
from app.aftersales.services.data_export import AftersalesExporter
from app.aftersales.services.order_references import oldest_order_label_age_seconds, refresh_order_labels
from app.aftersales.services.service_order import CANCELLED_EVENT as SERVICE_ORDER_CANCELLED_EVENT
from app.aftersales.services.service_order import CREATED_EVENT as SERVICE_ORDER_CREATED_EVENT

__all__ = [
    "SERVICE_ORDER_CANCELLED_EVENT",
    "SERVICE_ORDER_CREATED_EVENT",
    "AftersalesExporter",
    "ServiceOrderState",
    "oldest_order_label_age_seconds",
    "refresh_order_labels",
]
