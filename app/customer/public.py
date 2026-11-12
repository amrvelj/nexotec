"""The only surface other contexts may import from customer. Import-linter's
contract allows `app.<other-context>` to import `app.customer.public`, never
`app.customer.models` / `app.customer.services` / `app.customer.api` directly.
"""

from app.customer.models.customer import Customer, CustomerLifecycleStatus
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.ownership import CustomerOwnershipProvider
from app.customer.services.advisor_label import oldest_advisor_label_age_seconds, refresh_advisor_labels
from app.customer.services.customer import (
    CustomerSalesLabel,
    allocate_vehicle_party,
    channel_authorises_marketing,
    customer_display_name,
    customer_in_group,
    customer_is_merged,
    get_customer_or_404,
    get_customer_sales_labels,
    has_usable_domicile_address,
    list_customer_vehicles,
    list_vehicle_parties,
    list_vehicle_party_holders,
    oldest_vehicle_party_label_age_seconds,
    refresh_vehicle_party_labels,
    resolve_live_customer_id,
    set_credit_block,
)
from app.customer.services.legal_basis import has_any_basis_for_group
from app.customer.services.vehicle_party_counts import count_vehicle_parties_for_vehicles

__all__ = [
    "Customer",
    "CustomerLifecycleStatus",
    "CustomerOwnershipProvider",
    "CustomerSalesLabel",
    "VehicleParty",
    "VehiclePartyRole",
    "allocate_vehicle_party",
    "channel_authorises_marketing",
    "count_vehicle_parties_for_vehicles",
    "customer_display_name",
    "customer_in_group",
    "customer_is_merged",
    "get_customer_or_404",
    "get_customer_sales_labels",
    "has_any_basis_for_group",
    "has_usable_domicile_address",
    "list_customer_vehicles",
    "list_vehicle_parties",
    "list_vehicle_party_holders",
    "oldest_advisor_label_age_seconds",
    "oldest_vehicle_party_label_age_seconds",
    "refresh_advisor_labels",
    "refresh_vehicle_party_labels",
    "resolve_live_customer_id",
    "set_credit_block",
]
