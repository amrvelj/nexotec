"""A manual configuration's contract learns its pipeline stock item
(KAN-144, Anto 2026-10-04).

Confirming a contract for a manually configured vehicle makes Stock create a
pipeline stock item (inventory.services.pipeline); Stock names the contract
on `inventory.stock_item.added` (`originContractId`, `originRole`). Recording
that item on the contract lets the KAN-100 purchase gate apply to it like any
stock car: `SalesContract.is_invoiceable` is derived from the purchase
replica by stock item, so the contract becomes invoiceable once Stock books
the purchase, whichever of the two events arrives first.

The contract stays `vehicle_source = "manual"`: what was sold is still the
configuration; the stock item is where it is being fulfilled.
"""

import datetime as dt
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.sales.models.contract import SalesContract

logger = logging.getLogger(__name__)


def link_manual_configuration_to_pipeline_item(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    contract_id: uuid.UUID,
    stock_item_id: uuid.UUID,
    stock_item_label: str | None,
    refreshed_at: dt.datetime,
) -> None:
    """Flushes only: consume_once commits it with its processed_event row.
    Idempotent: the same link again changes nothing (no version bump). The
    tenant comes from the event (rule 7) — another dealership's item never
    attaches to this contract."""

    contract = db.scalar(
        select(SalesContract).where(SalesContract.tenant_id == tenant_id, SalesContract.id == contract_id)
    )
    if contract is None or contract.vehicle_source != "manual":
        logger.warning(
            "pipeline_link_skipped",
            extra={"contractId": str(contract_id), "stockItemId": str(stock_item_id), "reason": "no manual contract"},
        )
        return
    if contract.stock_item_id is not None and contract.stock_item_id != stock_item_id:
        # One manual configuration, one pipeline item (Stock's
        # (tenant_id, pipeline_ref) unique index). Another id here is an
        # integrity problem to surface, never to overwrite silently.
        logger.error(
            "pipeline_link_conflict",
            extra={
                "contractId": str(contract_id),
                "linkedStockItemId": str(contract.stock_item_id),
                "eventStockItemId": str(stock_item_id),
            },
        )
        return
    if contract.stock_item_id == stock_item_id and contract.stock_item_label == stock_item_label:
        return

    contract.stock_item_id = stock_item_id
    contract.stock_item_label = stock_item_label
    contract.stock_item_denorm_refreshed_at = refreshed_at
    contract.version += 1
    db.flush()
