"""The only surface other contexts may import from inventory. Import-linter's
contract allows `app.<other-context>` to import `app.inventory.public`, never
`app.inventory.models` / `app.inventory.services` / `app.inventory.api`
directly.

Empty of cross-context calls in PR-1 — `reserve`/`release` (PR-4) are the
first real entries, for WP-8's future Sales caller.
"""

from app.inventory.models.stock_item import LifecycleStatus, ReservationState, StockItem, StockItemCondition
from app.inventory.services.stock_item import get_stock_item_or_404

__all__ = [
    "LifecycleStatus",
    "ReservationState",
    "StockItem",
    "StockItemCondition",
    "get_stock_item_or_404",
]
