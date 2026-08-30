"""The only surface other contexts may import from inventory. Import-linter's
contract allows `app.<other-context>` to import `app.inventory.public`, never
`app.inventory.models` / `app.inventory.services` / `app.inventory.api`
directly.

`reserve`/`release` (PR-4) are the first real cross-context entries, for
WP-8's future Sales caller — each owns its own commit (ADR-047, Pattern
B); the caller supplies its own Idempotency-Key and calls from OUTSIDE
its own contract-write transaction.
"""

from app.inventory.models.stock_item import LifecycleStatus, ReservationState, StockItem, StockItemCondition
from app.inventory.services.reservation import release, reserve
from app.inventory.services.stock_item import get_stock_item_or_404

__all__ = [
    "LifecycleStatus",
    "ReservationState",
    "StockItem",
    "StockItemCondition",
    "get_stock_item_or_404",
    "release",
    "reserve",
]
