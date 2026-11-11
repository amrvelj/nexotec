"""Sales' side of a customer merge (KAN-185, ADR-047; Anto, 2026-10-31,
option A).

The customer context merges and publishes `customer.merged` in its own
transaction; it writes nothing of Sales'. Sales' consumer
(`sales.customer_merged`, `app/sales/consumers.py`) moves everything Sales
holds for the duplicate — the retired legacy `transaction` rows (ADR-050:
retired for business writes, but a merge still moves them), offers,
contracts and deals — to the survivor, in its own transaction with its
processed-event row. A failure is redelivered on the outbox's backoff and,
after its last attempt, dead-lettered with an alert (`app/core/outbox_worker.py`);
a replay re-runs it. No shared cross-context transaction remains (CLAUDE.md
rule 12). The nightly Sales reconciliation reports a row still naming a
customer merged longer ago than outbox lag (KAN-581, `app/sales/reconciliation.py`).

The moved offers, contracts and deal rows take the survivor's label (rule
2's denormalised `customer_label`) in the same flush (KAN-580): before, they kept the
duplicate's name, so the Sales list named a customer that no longer exists
as a record. The consumer reads the survivor through `customer.public`,
never from the event's payload (delivery is unordered; the payload's
survivor may be merged itself by now). `customer_locality` is left as it
stands: no Sales path writes it (Anto, 2026-11-10, KAN-590). A later rename
of the survivor reaches open work through the nightly
`sales.customer_labels` job (KAN-590).
`customer_language` (which language a regenerated document prints in) is
left as it stands by this consumer; on open work (a draft or open offer
without a signed contract of its own buyer, a pending contract) the nightly
`sales.customer_labels` job then gives it
the survivor's language, as for any customer whose language changed (KAN-590's
approved plan). A signed contract keeps the language it had.

Before KAN-185 the legacy rows moved inside the merge's own transaction (the
tracked ADR-047 debt) and offers/contracts/deals in an own-commit call after
it, whose failure nothing reported.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.sales.models.contract import SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.offer import SalesOffer
from app.sales.models.transaction import Transaction


@dataclass(frozen=True)
class SurvivorLabel:
    """What a moved row shows of its new customer — read by the consumer
    from `customer.public`, never from the event."""

    label: str


@dataclass(frozen=True)
class MergeRepointCounts:
    transactions: int
    offers: int
    contracts: int
    deals: int


def repoint_merged_customer_records(
    db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID, survivor: SurvivorLabel | None
) -> MergeRepointCounts:
    """Re-points every Sales row naming `duplicate_id` at `target_id`, and
    writes `survivor`'s label onto every moved offer, contract and deal row
    (KAN-580). `survivor` is None only when the consumer could not read the
    survivor in the group (a chain that left it): the rows still move and
    keep the label they had.

    Flushes only; the consumer's `consume_once` commits it together with the
    processed-event row. A second run finds nothing left to move.
    """

    refreshed_at = utcnow()

    def relabel(row: SalesOffer | SalesContract | SalesDeal) -> None:
        if survivor is None:
            return
        row.customer_label = survivor.label
        row.customer_denorm_refreshed_at = refreshed_at

    transactions = list(db.scalars(select(Transaction).where(Transaction.customer_id == duplicate_id)).all())
    for txn in transactions:
        txn.customer_id = target_id
    db.flush()

    # Row locks in the order every other Sales writer takes them — offer,
    # then contract, then deal (create_contract locks the offer before it
    # writes the deal, KAN-147). One flush would let the unit of work write
    # sales_deal first and deadlock against a contract being created from
    # one of these offers.
    offers = list(db.scalars(select(SalesOffer).where(SalesOffer.customer_id == duplicate_id)).all())
    for offer in offers:
        offer.customer_id = target_id
        relabel(offer)
    db.flush()

    contracts = list(db.scalars(select(SalesContract).where(SalesContract.customer_id == duplicate_id)).all())
    for contract in contracts:
        contract.customer_id = target_id
        relabel(contract)
    db.flush()

    deals = list(db.scalars(select(SalesDeal).where(SalesDeal.customer_id == duplicate_id)).all())
    for deal in deals:
        deal.customer_id = target_id
        relabel(deal)
    db.flush()

    return MergeRepointCounts(
        transactions=len(transactions), offers=len(offers), contracts=len(contracts), deals=len(deals)
    )
