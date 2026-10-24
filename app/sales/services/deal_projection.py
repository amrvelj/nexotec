"""The ONE writer of `sales_deal` (WP-8 PR-1, ADR-060). Every offer/contract
mutation that could change what the grid shows calls this, in the same
local transaction as its own write — see the module docstring on
app.sales.models.deal for why a materialized table beats a query-time
union, and why `id` is the stable offer-or-contract identity rather than a
fresh id per row.

A row is always projected from its deal's LIVE entity, whichever side the
caller wrote: the offer's live contract (one that is not cancelled — at
most one, KAN-147) while it has one, else the offer itself (KAN-408,
KAN-409; Anto, 2026-10-21, option A both times). So an offer-side write
after a contract exists re-projects the contract, never the offer's buyer,
car or documents onto the contract's row; and a cancelled contract hands
the row back to its offer, which is open work again — the cancelled
contract stays on the offer's and the customer's history. A contract
started without an offer is its own deal and keeps its row, cancelled or
not.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.document import DocumentOwnerType
from app.sales.models.offer import SalesOffer
from app.sales.services.document import count_documents


def live_contract_of(db: Session, offer: SalesOffer) -> SalesContract | None:
    """The offer's contract that is not cancelled, if any (KAN-147 keeps it
    to one)."""

    return db.scalar(
        select(SalesContract)
        .where(
            SalesContract.tenant_id == offer.tenant_id,
            SalesContract.offer_id == offer.id,
            SalesContract.status != ContractStatus.CANCELLED,
        )
        .order_by(SalesContract.created_at.desc())
        .limit(1)
    )


def upsert_deal_projection(
    db: Session, *, offer: SalesOffer | None = None, contract: SalesContract | None = None
) -> SalesDeal:
    if contract is None and offer is None:
        raise ValueError("upsert_deal_projection requires an offer or a contract")

    # Write the caller's pending offer/contract changes BEFORE the deal row:
    # every Sales writer takes row locks offer -> contract -> deal
    # (create_contract holds the offer's lock while it writes the deal,
    # KAN-147). Left to one flush, the unit of work orders sales_deal before
    # sales_offer and deadlocks against that creation.
    db.flush()

    if contract is not None:
        if contract.status != ContractStatus.CANCELLED or contract.offer_id is None:
            return _project_contract(db, contract)
        # KAN-409: the cancellation freed the offer; it is the live entity.
        freed = db.get(SalesOffer, contract.offer_id)
        if freed is None:
            return _project_contract(db, contract)
        offer = freed
    assert offer is not None
    live = live_contract_of(db, offer)
    if live is not None:
        # KAN-408: the contract's row is the contract's alone.
        return _project_contract(db, live)
    return _project_offer(db, offer)


def _project_contract(db: Session, contract: SalesContract) -> SalesDeal:
    deal_id = contract.offer_id if contract.offer_id is not None else contract.id
    deal = db.get(SalesDeal, deal_id)
    if deal is None:
        deal = SalesDeal(id=deal_id, tenant_id=contract.tenant_id)
        db.add(deal)
    deal.entity_type = "contract"
    deal.number = contract.contract_number
    deal.status = contract.status.value
    deal.offer_id = contract.offer_id
    deal.offer_number = contract.offer_number
    deal.contract_id = contract.id
    deal.contract_number = contract.contract_number
    deal.customer_id = contract.customer_id
    deal.customer_label = contract.customer_label
    deal.customer_locality = contract.customer_locality
    deal.customer_denorm_refreshed_at = contract.customer_denorm_refreshed_at
    deal.vehicle_label = contract.vehicle_label
    # KAN-200: the responsible seller of the live entity.
    deal.owner_user_id = contract.owner_user_id
    deal.owner_label = contract.owner_label
    deal.owner_label_refreshed_at = contract.owner_label_refreshed_at
    deal.gross_price = contract.gross_price
    deal.margin = contract.margin
    offer_docs = (
        count_documents(db, tenant_id=contract.tenant_id, owner_type=DocumentOwnerType.OFFER, owner_id=contract.offer_id)
        if contract.offer_id is not None
        else 0
    )
    contract_docs = count_documents(
        db, tenant_id=contract.tenant_id, owner_type=DocumentOwnerType.CONTRACT, owner_id=contract.id
    )
    deal.documents_count = offer_docs + contract_docs
    db.flush()
    return deal


def _project_offer(db: Session, offer: SalesOffer) -> SalesDeal:
    deal = db.get(SalesDeal, offer.id)
    if deal is None:
        deal = SalesDeal(id=offer.id, tenant_id=offer.tenant_id)
        db.add(deal)
    deal.entity_type = "offer"
    deal.number = offer.offer_number
    deal.status = offer.status.value
    deal.offer_id = offer.id
    deal.offer_number = offer.offer_number
    # A row handed back by a cancelled contract (KAN-409) drops it: the
    # row is the offer's again, as before the contract was made.
    deal.contract_id = None
    deal.contract_number = None
    deal.customer_id = offer.customer_id
    deal.customer_label = offer.customer_label
    deal.customer_locality = offer.customer_locality
    deal.customer_denorm_refreshed_at = offer.customer_denorm_refreshed_at
    deal.vehicle_label = offer.vehicle_label
    # KAN-200: the responsible seller of the live entity.
    deal.owner_user_id = offer.owner_user_id
    deal.owner_label = offer.owner_label
    deal.owner_label_refreshed_at = offer.owner_label_refreshed_at
    deal.gross_price = offer.gross_price
    # An offer's row has never carried a margin (the grid's margin is the
    # contract's); one handed back by a cancelled contract drops its figure.
    deal.margin = None
    deal.documents_count = count_documents(
        db, tenant_id=offer.tenant_id, owner_type=DocumentOwnerType.OFFER, owner_id=offer.id
    )
    db.flush()
    return deal
