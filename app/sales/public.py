"""The only surface other contexts may import from sales. Import-linter's
contract allows `app.<other-context>` to import `app.sales.public`, never
`app.sales.models` / `app.sales.services` / `app.sales.api` directly.

`SalesOwnershipProvider` (KAN-200) is the ownership provider the composition
root `app.ownership_registry` registers with `app.core.ownership`; platform
reaches it only through that registry.

`refresh_owner_labels` / `oldest_owner_label_age_seconds` (KAN-404) are the
worker's nightly responsible-seller label refresh and its sync-age gauge.

`get_contract_statuses` (KAN-122) is Stock's read for its nightly
reservation sweep: plain statuses, never an ORM row, and the caller runs it
on a session of its own (ADR-047).

`contract_with_invoice_requested_for_stock_item` (KAN-146) is Stock's read
before it stornos a purchase: a contract on the car whose invoice was
requested, by number. A SELECT only, on the caller's own session.

`get_trade_in_seller` (KAN-189) is the customer context's read when a trade-in
is bought: the contract's customer and since when it is known, plain data.
`referenced_configuration_ids` (KAN-270) is vehicle's read for its daily purge of
configurations no host ever took: which of the ids it names any offer or
contract holds. A SELECT only.
"""

from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.offer import SalesOffer
from app.sales.models.transaction import Transaction
from app.sales.services.configuration_reference import referenced_configuration_ids
from app.sales.services.contract_status import (
    contract_with_invoice_requested_for_stock_item,
    get_contract_statuses,
)
from app.sales.services.numbering import legacy_offer_counters
from app.sales.services.owner_label import oldest_owner_label_age_seconds, refresh_owner_labels
from app.sales.services.ownership import SalesOwnershipProvider
from app.sales.services.signed_trade_ins import (
    valuations_carried_by_pending_contracts,
    valuations_carried_by_signed_contracts,
)
from app.sales.services.trade_in_seller import TradeInSeller, get_trade_in_seller

__all__ = [
    "ContractStatus",
    "SalesContract",
    "SalesOffer",
    "SalesOwnershipProvider",
    "TradeInSeller",
    "Transaction",
    "contract_with_invoice_requested_for_stock_item",
    "get_contract_statuses",
    "get_trade_in_seller",
    "legacy_offer_counters",
    "oldest_owner_label_age_seconds",
    "referenced_configuration_ids",
    "refresh_owner_labels",
    "valuations_carried_by_pending_contracts",
    "valuations_carried_by_signed_contracts",
]
