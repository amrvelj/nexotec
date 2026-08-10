"""The only surface other contexts may import from platform. Import-linter's
contract allows `app.<other-context>` to import `app.platform.public`, never
`app.platform.models` / `app.platform.services` / `app.platform.api` directly.
"""

from app.platform.models.dealer import Dealer
from app.platform.models.user import User
from app.platform.services.dealer import get_dealer_or_404
from app.platform.services.reference_data import get_reference_list_or_404, get_reference_value_or_404
from app.platform.services.user import get_user_or_404

__all__ = [
    "Dealer",
    "User",
    "get_dealer_or_404",
    "get_reference_list_or_404",
    "get_reference_value_or_404",
    "get_user_or_404",
]
