"""Registers every context's ownership provider with `app.core.ownership`
(KAN-200; Dealer Administration FR-A-37, D-A-35). A composition root
directly under app/, like app.model_registry: the one place that may name
every context's provider, so that platform's deactivation step asks the
registry and imports no context, and no context imports platform.

Imported by app.main (the API) and app.worker (the `valid_to` expiry job
deactivates through the same step). Importing it twice is harmless: the
registry replaces by module.
"""

from app.aftersales.ownership import AftersalesOwnershipProvider
from app.core.ownership import register_ownership_provider
from app.customer.public import CustomerOwnershipProvider
from app.inventory.public import InventoryOwnershipProvider
from app.parts.ownership import PartsOwnershipProvider
from app.sales.public import SalesOwnershipProvider


def register_ownership_providers() -> None:
    for provider in (
        CustomerOwnershipProvider(),
        SalesOwnershipProvider(),
        InventoryOwnershipProvider(),
        AftersalesOwnershipProvider(),
        PartsOwnershipProvider(),
    ):
        register_ownership_provider(provider)


register_ownership_providers()
