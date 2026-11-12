"""The only surface other contexts may import from inventory. Import-linter's
contract allows `app.<other-context>` to import `app.inventory.public`, never
`app.inventory.models` / `app.inventory.services` / `app.inventory.api`
directly.

`reserve_for_contract`/`release` (PR-4, KAN-114) are Sales' cross-context
entries — each owns its own commit (ADR-047, Pattern B); the caller
supplies its own Idempotency-Key and calls from OUTSIDE its own
contract-write transaction. `reserve_for_contract` is idempotent by (item,
contract) rather than by key alone, so a retry after a compensating release
reserves again. `reserve` (key-replay idempotency) has no caller outside
inventory since KAN-114. Both `reserve` and `release` require the caller's
key; the HTTP endpoints use the service's own `*_without_key` entries,
because their IdempotentRoute holds the client's key (KAN-266).

`get_stock_item_pricing` (WP-8 PR-3) is a second, read-only Sales entry —
a plain dict, never an ORM row, matching app.vehicle.public's own
"getter returns a dict" posture for exactly the same reason: the caller
must not be able to hold or mutate an inventory object across a context
boundary. `get_stock_item_vehicle` (KAN-523) is a third: which vehicle-mdm
record (and VIN) a stock item is, so Sales can hand the sold car's plate on.
`referenced_configuration_ids` (KAN-270) is vehicle's: which of the
configurations it names a stock item holds, for its daily purge of the
configurations no host ever took. A SELECT only. `get_stock_numbers`
(KAN-157) is Sales' label backfill: stock numbers by id, plain data.
`is_stock_item_purchased` (KAN-146) is Sales' storno consumer's re-read of the
purchase fact as it is now (delivery is unordered); a SELECT only, a bool.
`SALES_CONTRACT_PROJECTION_CONSUMERS` (KAN-630) is the worker's list of
Stock's consumers of Sales' contract facts; `apply_backfilled_contract` and
`record_backfill` are the projection backfill's writes
(scripts/backfill_inventory_sales_contract_projection.py), commit-free;
`backfill_recorded` (KAN-678) is whether a run of it has been committed —
the legacy transaction import re-runs it only then. A SELECT only, a bool.
"""

from app.inventory.models.stock_item import LifecycleStatus, ReservationState, StockItem, StockItemCondition
from app.inventory.ownership import InventoryOwnershipProvider
from app.inventory.services.configuration_label import (
    oldest_configuration_label_age_seconds,
    refresh_configuration_labels,
)
from app.inventory.services.configuration_reference import referenced_configuration_ids
from app.inventory.services.group_listing import get_stock_items_for_vehicles
from app.inventory.services.pricing import get_stock_item_pricing
from app.inventory.services.reservation import release, reserve, reserve_for_contract
from app.inventory.services.sales_contract import CONSUMERS as SALES_CONTRACT_PROJECTION_CONSUMERS
from app.inventory.services.sales_contract import apply_backfilled_contract, backfill_recorded, record_backfill
from app.inventory.services.stock_item import get_stock_item_or_404, get_stock_numbers, is_stock_item_purchased
from app.inventory.services.valuation import set_valuation_ref
from app.inventory.services.vehicle_link import get_stock_item_vehicle

__all__ = [
    "SALES_CONTRACT_PROJECTION_CONSUMERS",
    "InventoryOwnershipProvider",
    "LifecycleStatus",
    "ReservationState",
    "StockItem",
    "StockItemCondition",
    "apply_backfilled_contract",
    "backfill_recorded",
    "get_stock_item_or_404",
    "get_stock_item_pricing",
    "get_stock_item_vehicle",
    "get_stock_items_for_vehicles",
    "get_stock_numbers",
    "is_stock_item_purchased",
    "oldest_configuration_label_age_seconds",
    "record_backfill",
    "referenced_configuration_ids",
    "refresh_configuration_labels",
    "release",
    "reserve",
    "reserve_for_contract",
    "set_valuation_ref",
]
