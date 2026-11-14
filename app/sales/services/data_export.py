"""Sales' export declaration (KAN-218; FR-A-44): offers, contracts, deals and
their line items, and every issued document as a PDF with its number and
date — re-rendered from the frozen content each generation stored, through
the one renderer (`render_document_pdf`), never a second one. Sales tells
the export which customers its rows name, so the group-owned customers come
for the Betrieb's own relationship (ADR-014).
"""

import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.export import ExportDocument, ExportedTable, ExportScope, TableExporter, table_of
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


def _renderer(db: Session, document: SalesDocument, scope: ExportScope) -> Callable[[], bytes]:
    def render() -> bytes:
        return render_document_pdf(db, document=document, dealership_id=scope.dealership_id)

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
        numbers: dict[tuple[DocumentOwnerType, uuid.UUID], str | None] = {}
        for owner_id, number in db.execute(
            select(SalesOffer.id, SalesOffer.offer_number).where(SalesOffer.tenant_id == scope.dealership_id)
        ):
            numbers[(DocumentOwnerType.OFFER, owner_id)] = number
        for owner_id, number in db.execute(
            select(SalesContract.id, SalesContract.contract_number).where(
                SalesContract.tenant_id == scope.dealership_id
            )
        ):
            numbers[(DocumentOwnerType.CONTRACT, owner_id)] = number
        documents = db.scalars(
            select(SalesDocument)
            .where(SalesDocument.tenant_id == scope.dealership_id)
            .order_by(SalesDocument.rendered_at, SalesDocument.id)
        ).all()
        for document in documents:
            number = numbers.get((document.owner_type, document.owner_id))
            label = (number or str(document.owner_id)).replace("/", "-").replace(" ", "_")
            day = document.rendered_at.date().isoformat()
            yield ExportDocument(
                path=f"documents/sales/{document.owner_type.value}-{label}-v{document.version}-{day}.pdf",
                kind=f"sales_{document.owner_type.value}",
                number=number,
                issued_at=document.rendered_at,
                render=_renderer(db, document, scope),
            )
