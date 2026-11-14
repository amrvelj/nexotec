"""Sales' export declaration (KAN-218; FR-A-44): offers, contracts, deals and
their line items, and every issued document as a PDF with its number and
date — re-rendered from the frozen content each generation stored, through
the one renderer (`render_document_pdf`), never a second one. Sales tells
the export which customers its rows name, so the group-owned customers come
for the Betrieb's own relationship (ADR-014).
"""

import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.export import ROW_BATCH, ExportDocument, ExportedTable, ExportScope, TableExporter, table_of
from app.sales.models.contract import SalesContract
from app.sales.models.deal import SalesDeal, SalesNumberSequence
from app.sales.models.document import DocumentOwnerType, SalesDocument
from app.sales.models.line_item import SalesLineItem
from app.sales.models.offer import SalesOffer
from app.sales.models.stock_item_purchase import SalesStockItemPurchase
from app.sales.services.document import render_document_pdf


def _tenant(model) -> ExportedTable:
    table = table_of(model)
    return ExportedTable(table=table, scope=lambda scope: table.c.tenant_id == scope.dealership_id)


def _renderer(db: Session, document_id: uuid.UUID, scope: ExportScope) -> Callable[[], bytes]:
    """KAN-715: the document row is loaded for its own render and let go
    after it, so a Betrieb's years of documents never sit in the session."""

    def render() -> bytes:
        document = db.get(SalesDocument, document_id)
        assert document is not None  # read in the export's one snapshot
        try:
            return render_document_pdf(db, document=document, dealership_id=scope.dealership_id)
        finally:
            db.expunge(document)

    return render


class SalesExporter(TableExporter):
    context = "sales"

    def exported_tables(self) -> Sequence[ExportedTable]:
        return tuple(
            _tenant(model)
            for model in (
                SalesOffer,
                SalesContract,
                SalesDeal,
                SalesLineItem,
                SalesDocument,
                SalesNumberSequence,
                SalesStockItemPurchase,
            )
        )

    def references(self, db: Session, scope: ExportScope) -> Mapping[str, frozenset[uuid.UUID]]:
        ids: set[uuid.UUID] = set()
        for model in (SalesOffer, SalesContract, SalesDeal):
            ids.update(
                db.scalars(
                    select(model.customer_id).where(
                        model.tenant_id == scope.dealership_id, model.customer_id.is_not(None)
                    )
                )
            )
        return {"customer": frozenset(ids)}

    def documents(self, db: Session, scope: ExportScope) -> Iterable[ExportDocument]:
        """Every issued offer and contract document, oldest first, streamed
        in batches (KAN-715): the owner's number comes from the same query,
        never from a dict of every offer and contract."""

        number = func.coalesce(SalesOffer.offer_number, SalesContract.contract_number)
        rows = db.execute(
            select(
                SalesDocument.id, SalesDocument.owner_type, SalesDocument.owner_id, SalesDocument.version,
                SalesDocument.rendered_at, number,
            )
            .outerjoin(
                SalesOffer,
                and_(SalesDocument.owner_type == DocumentOwnerType.OFFER, SalesOffer.id == SalesDocument.owner_id),
            )
            .outerjoin(
                SalesContract,
                and_(
                    SalesDocument.owner_type == DocumentOwnerType.CONTRACT, SalesContract.id == SalesDocument.owner_id
                ),
            )
            .where(SalesDocument.tenant_id == scope.dealership_id)
            .order_by(SalesDocument.rendered_at, SalesDocument.id)
            .execution_options(yield_per=ROW_BATCH)
        )
        for document_id, owner_type, owner_id, version, rendered_at, owner_number in rows:
            label = (owner_number or str(owner_id)).replace("/", "-").replace(" ", "_")
            day = rendered_at.date().isoformat()
            yield ExportDocument(
                path=f"documents/sales/{owner_type.value}-{label}-v{version}-{day}.pdf",
                kind=f"sales_{owner_type.value}",
                number=owner_number,
                issued_at=rendered_at,
                render=_renderer(db, document_id, scope),
            )
