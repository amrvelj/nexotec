"""The only surface other contexts may import from platform. Import-linter's
contract allows `app.<other-context>` to import `app.platform.public`, never
`app.platform.models` / `app.platform.services` / `app.platform.api` directly.
"""

from app.platform.models.dealership import DealerGroup, Dealership, Location
from app.platform.models.user import User
from app.platform.services.dealership import get_dealership_or_404
from app.platform.services.reference_data import get_reference_list_or_404, get_reference_value_or_404
from app.platform.services.user import get_user_or_404

__all__ = [
    "DealerGroup",
    "Dealership",
    "Location",
    "User",
    "get_dealership_or_404",
    "get_reference_list_or_404",
    "get_reference_value_or_404",
    "get_user_or_404",
]
