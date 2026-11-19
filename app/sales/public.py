"""The only surface other contexts may import from sales. Import-linter's
contract allows `app.<other-context>` to import `app.sales.public`, never
`app.sales.models` / `app.sales.services` / `app.sales.api` directly.

No mapped class leaves this module (KAN-123 step 4, ADR-162). What
valuation's and Stock's nightly reconciliation check against Sales'
`sales_contract` table is built here (`contract_reference_check`,
`signed_contract_carries_valuation`, `contract_carries_valuation` with
`CarryState`, `contract_changed_since`, `uncarried_contract_carries_valuation`,
`contract_differs_from_projection`, `unprojected_contract_check`;
services/reconciliation_targets.py) and confined to the reconciliation
modules by an architecture test. `ContractStatus` stays: an enum, not a table.

`SalesOwnershipProvider` (KAN-200) is the ownership provider the composition
root `app.ownership_registry` registers with `app.core.ownership`; platform
reaches it only through that registry.

`refresh_owner_labels` / `oldest_owner_label_age_seconds` (KAN-404) are the
worker's nightly responsible-seller label refresh and its sync-age gauge.
`refresh_customer_labels` / `oldest_customer_label_age_seconds` (KAN-590) are
the same for the customer's label on open offers, pending contracts and their
deal rows.

`list_contract_facts` / `ContractFacts` (KAN-630) are the read of Stock's
projection backfill (scripts/backfill_inventory_sales_contract_projection.py):
every contract's status, number, car and invoice request, as plain data, a
page at a time. A SELECT only. Stock itself never calls Sales (ADR-131): its
sweep and «Einkauf stornieren» read the projection its consumers keep.

`get_trade_in_seller` (KAN-189) answers a trade-in contract's customer and
since when it is known, plain data, for
`scripts/backfill_trade_in_seller_on_stock_items.py` (KAN-674), which fills
the seller onto a pipeline item created before KAN-632. The worker's fallback
that asked it for an event without the seller is retired (KAN-674). Customer,
below sales, never calls it (ADR-131).
`referenced_configuration_ids` (KAN-270) is vehicle's read for its daily purge of
configurations no host ever took: which of the ids it names any offer or
contract holds. A SELECT only.

`trade_in_valuation_carries` (KAN-631) is the valuation backfill's read of
which contracts carry which trade-in valuation, pending or signed — plain
data. Valuation itself no longer asks Sales (ADR-131): Sales tells it, through
`valuation.public`'s carry calls.

`register_message_types` (KAN-217) registers Sales' three customer message
types (offer, order confirmation, sales contract) with platform's send-dialog
registry; the composition root `app/message_type_registry.py` calls it at
start-up. Writes nothing.
"""

from app.sales.message_types import register_message_types
from app.sales.models.contract import ContractStatus
from app.sales.services.configuration_reference import referenced_configuration_ids
from app.sales.services.contract_status import ContractFacts, list_contract_facts
from app.sales.services.customer_label import oldest_customer_label_age_seconds, refresh_customer_labels
from app.sales.services.data_export import SalesExporter
from app.sales.services.owner_label import oldest_owner_label_age_seconds, refresh_owner_labels
from app.sales.services.ownership import SalesOwnershipProvider
from app.sales.services.reconciliation_targets import (
    CarryState,
    contract_carries_valuation,
    contract_changed_since,
    contract_differs_from_projection,
    contract_reference_check,
    signed_contract_carries_valuation,
    uncarried_contract_carries_valuation,
    unprojected_contract_check,
)
from app.sales.services.trade_in_seller import TradeInSeller, get_trade_in_seller
from app.sales.services.valuation_carry import ContractValuationCarry, trade_in_valuation_carries

__all__ = [
    "CarryState",
    "ContractFacts",
    "ContractStatus",
    "ContractValuationCarry",
    "SalesExporter",
    "SalesOwnershipProvider",
    "TradeInSeller",
    "contract_carries_valuation",
    "contract_changed_since",
    "contract_differs_from_projection",
    "contract_reference_check",
    "get_trade_in_seller",
    "list_contract_facts",
    "oldest_customer_label_age_seconds",
    "oldest_owner_label_age_seconds",
    "referenced_configuration_ids",
    "refresh_customer_labels",
    "refresh_owner_labels",
    "register_message_types",
    "signed_contract_carries_valuation",
    "trade_in_valuation_carries",
    "uncarried_contract_carries_valuation",
    "unprojected_contract_check",
]
