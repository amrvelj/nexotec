"""The only surface other contexts may import from customer. Import-linter's
contract allows `app.<other-context>` to import `app.customer.public`, never
`app.customer.models` / `app.customer.services` / `app.customer.api` directly.
"""

from app.customer.models.customer import Customer
from app.customer.models.vehicle_party import VehicleParty
from app.customer.services.customer import get_customer_or_404, repoint_vehicle_party
from app.customer.services.legal_basis import has_any_basis_for_group

__all__ = [
    "Customer",
    "VehicleParty",
    "get_customer_or_404",
    "has_any_basis_for_group",
    "repoint_vehicle_party",
]
