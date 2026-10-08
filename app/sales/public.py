"""The only surface other contexts may import from sales. Import-linter's
contract allows `app.<other-context>` to import `app.sales.public`, never
`app.sales.models` / `app.sales.services` / `app.sales.api` directly.

`get_contract_statuses` (KAN-122) is Stock's read for its nightly
reservation sweep: plain statuses, never an ORM row, and the caller runs it
on a session of its own (ADR-047).
"""

from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.offer import SalesOffer
from app.sales.models.transaction import Transaction
from app.sales.services.contract_status import get_contract_statuses
from app.sales.services.customer_merge import repoint_customer_sales_records
from app.sales.services.signed_trade_ins import valuations_carried_by_signed_contracts
from app.sales.services.transaction import repoint_customer_transactions

__all__ = [
    "ContractStatus",
    "SalesContract",
    "SalesOffer",
    "Transaction",
    "get_contract_statuses",
    "repoint_customer_sales_records",
    "repoint_customer_transactions",
    "valuations_carried_by_signed_contracts",
]
