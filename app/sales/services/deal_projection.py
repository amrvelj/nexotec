"""The ONE writer of `sales_deal` (WP-8 PR-1, ADR-060). Every offer/contract
mutation that could change what the grid shows calls this, in the same
local transaction as its own write — see the module docstring on
app.sales.models.deal for why a materialized table beats a query-time
union, and why `id` is the stable offer-or-contract identity rather than a
fresh id per row.
"""

from sqlalchemy.orm import Session

from app.sales.models.contract import SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.document import DocumentOwnerType
from app.sales.models.offer import SalesOffer
from app.sales.services.document import count_documents


def upsert_deal_projection(
    db: Session, *, offer: SalesOffer | None = None, contract: SalesContract | None = None
) -> SalesDeal:
    if contract is None and offer is None:
        raise ValueError("upsert_deal_projection requires an offer or a contract")

    if contract is not None:
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

    assert offer is not None
    deal = db.get(SalesDeal, offer.id)
    if deal is None:
        deal = SalesDeal(id=offer.id, tenant_id=offer.tenant_id)
        db.add(deal)
    # Once a contract exists for this deal, the offer's own row is never
    # upserted back into "offer" shape again — an offer-side call arriving
    # here after its contract exists would only happen from a caller bug
    # (the offer itself is not editable once superseded), so this is a
    # defensive guard, not a real code path.
    if deal.contract_id is None:
        deal.entity_type = "offer"
        deal.number = offer.offer_number
        deal.status = offer.status.value
        deal.gross_price = offer.gross_price
    deal.offer_id = offer.id
    deal.offer_number = offer.offer_number
    deal.customer_id = offer.customer_id
    deal.customer_label = offer.customer_label
    deal.customer_locality = offer.customer_locality
    deal.customer_denorm_refreshed_at = offer.customer_denorm_refreshed_at
    deal.vehicle_label = offer.vehicle_label
    deal.documents_count = count_documents(
        db, tenant_id=offer.tenant_id, owner_type=DocumentOwnerType.OFFER, owner_id=offer.id
    )
    db.flush()
    return deal
