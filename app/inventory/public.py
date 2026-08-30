"""The only surface other contexts may import from inventory. Import-linter's
contract allows `app.<other-context>` to import `app.inventory.public`, never
`app.inventory.models` / `app.inventory.services` / `app.inventory.api`
directly.

`reserve`/`release` (PR-4) are the first real cross-context entries, for
WP-8's future Sales caller — each owns its own commit (ADR-047, Pattern
B); the caller supplies its own Idempotency-Key and calls from OUTSIDE
its own contract-write transaction.

`get_stock_item_pricing` (WP-8 PR-3) is a second, read-only Sales entry —
a plain dict, never an ORM row, matching app.vehicle.public's own
"getter returns a dict" posture for exactly the same reason: the caller
must not be able to hold or mutate an inventory object across a context
boundary.
"""

from app.inventory.models.stock_item import LifecycleStatus, ReservationState, StockItem, StockItemCondition
from app.inventory.services.pricing import get_stock_item_pricing
from app.inventory.services.reservation import release, reserve
from app.inventory.services.stock_item import get_stock_item_or_404

__all__ = [
    "LifecycleStatus",
    "ReservationState",
    "StockItem",
    "StockItemCondition",
    "get_stock_item_or_404",
    "get_stock_item_pricing",
    "release",
    "reserve",
]
