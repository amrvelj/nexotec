"""The only surface other contexts may import from inventory. Import-linter's
contract allows `app.<other-context>` to import `app.inventory.public`, never
`app.inventory.models` / `app.inventory.services` / `app.inventory.api`
directly.

`reserve_for_contract`/`release` (PR-4, KAN-114) are Sales' cross-context
entries — each owns its own commit (ADR-047, Pattern B); the caller
supplies its own Idempotency-Key and calls from OUTSIDE its own
contract-write transaction. `reserve_for_contract` is idempotent by (item,
contract) rather than by key alone, so a retry after a compensating release
reserves again. `reserve` (key-replay idempotency when a key is passed) has
no caller outside inventory since KAN-114; the HTTP endpoints call `reserve`
and `release` without a key, because their IdempotentRoute holds the
client's (KAN-266).

`get_stock_item_pricing` (WP-8 PR-3) is a second, read-only Sales entry —
a plain dict, never an ORM row, matching app.vehicle.public's own
"getter returns a dict" posture for exactly the same reason: the caller
must not be able to hold or mutate an inventory object across a context
boundary.
"""

from app.inventory.models.stock_item import LifecycleStatus, ReservationState, StockItem, StockItemCondition
from app.inventory.services.group_listing import get_stock_items_for_vehicles
from app.inventory.services.pricing import get_stock_item_pricing
from app.inventory.services.reservation import release, reserve, reserve_for_contract
from app.inventory.services.stock_item import get_stock_item_or_404
from app.inventory.services.valuation import set_valuation_ref

__all__ = [
    "LifecycleStatus",
    "ReservationState",
    "StockItem",
    "StockItemCondition",
    "get_stock_item_or_404",
    "get_stock_item_pricing",
    "get_stock_items_for_vehicles",
    "release",
    "reserve",
    "reserve_for_contract",
    "set_valuation_ref",
]
