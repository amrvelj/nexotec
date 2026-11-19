"""The only surface other contexts may import from customer. Import-linter's
contract allows `app.<other-context>` to import `app.customer.public`, never
`app.customer.models` / `app.customer.services` / `app.customer.api` directly.

It hands out values, never a mapped class (KAN-123 step 5, ADR-162): a
customer is a `CustomerRef`, a vehicle party a `VehiclePartyRef`; what another
context's reconciliation checks against `customer` is built here too
(`services/reconciliation_targets.py`).

`message_recipient` / `preselected_message_channel` (KAN-217) are the send
dialog's read of a customer: where a message on e-mail, SMS or WhatsApp goes
and Customers' consent verdict for a transactional or marketing send (a
`MessageRecipient`, plain values, the refusal as a stable code), and the
channel the dialog opens on. SELECTs only.
"""

from app.customer.models.customer import CustomerLifecycleStatus
from app.customer.models.vehicle_party import VehiclePartyRole
from app.customer.ownership import CustomerOwnershipProvider
from app.customer.schemas.customer import CustomerVehiclePage
from app.customer.services.advisor_label import oldest_advisor_label_age_seconds, refresh_advisor_labels
from app.customer.services.contact_channels import channel_authorises_marketing
from app.customer.services.customer import has_usable_domicile_address
from app.customer.services.customer_refs import (
    CustomerRef,
    VehiclePartyRef,
    allocate_vehicle_party,
    customer_display_name,
    get_customer_or_404,
    list_vehicle_party_holders,
)
from app.customer.services.data_export import CustomerExporter
from app.customer.services.legal_basis import has_any_basis_for_group
from app.customer.services.message_recipient import (
    MessageRecipient,
    message_recipient,
    preselected_message_channel,
)
from app.customer.services.reconciliation_targets import customer_exists, customer_reference_check
from app.customer.services.vehicle_page import StockItemsFor, customer_vehicle_page
from app.customer.services.vehicle_party import resolve_live_customer_id
from app.customer.services.vehicle_party_labels import (
    oldest_vehicle_party_label_age_seconds,
    refresh_vehicle_party_labels,
)
from app.customer.services.vehicle_party_reads import (
    CustomerSalesLabel,
    customer_in_group,
    customer_is_merged,
    get_customer_sales_labels,
)

__all__ = [
    "CustomerExporter",
    "CustomerLifecycleStatus",
    "CustomerOwnershipProvider",
    "CustomerRef",
    "CustomerSalesLabel",
    "CustomerVehiclePage",
    "MessageRecipient",
    "StockItemsFor",
    "VehiclePartyRef",
    "VehiclePartyRole",
    "allocate_vehicle_party",
    "channel_authorises_marketing",
    "customer_display_name",
    "customer_exists",
    "customer_in_group",
    "customer_is_merged",
    "customer_reference_check",
    "customer_vehicle_page",
    "get_customer_or_404",
    "get_customer_sales_labels",
    "has_any_basis_for_group",
    "has_usable_domicile_address",
    "list_vehicle_party_holders",
    "message_recipient",
    "oldest_advisor_label_age_seconds",
    "oldest_vehicle_party_label_age_seconds",
    "preselected_message_channel",
    "refresh_advisor_labels",
    "refresh_vehicle_party_labels",
    "resolve_live_customer_id",
]
