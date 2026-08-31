"""The only surface other contexts may import from customer. Import-linter's
contract allows `app.<other-context>` to import `app.customer.public`, never
`app.customer.models` / `app.customer.services` / `app.customer.api` directly.
"""

from app.customer.models.customer import Customer, CustomerLifecycleStatus
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.services.customer import (
    allocate_vehicle_party,
    get_customer_or_404,
    list_customer_vehicles,
    list_vehicle_parties,
    repoint_vehicle_party,
    set_credit_block,
)
from app.customer.services.legal_basis import has_any_basis_for_group

__all__ = [
    "Customer",
    "CustomerLifecycleStatus",
    "VehicleParty",
    "VehiclePartyRole",
    "allocate_vehicle_party",
    "get_customer_or_404",
    "has_any_basis_for_group",
    "list_customer_vehicles",
    "list_vehicle_parties",
    "repoint_vehicle_party",
    "set_credit_block",
]
