"""Document generation (WP-8 PR-7) — builds a ContentDefinition from
app.platform.public's shared block vocabulary (WP-6b) and calls
render_document; never a second, local renderer. version-on-generation-
never-on-edit: a changed offer generates a NEW SalesDocument row with the
next version, the old one stays exactly as it was.

Margin, cost_basis and trade_in_purchase_price never appear in ANY block
built here — the seller-only figures live beside the document (the
margin panel, PR-8), never on it. Enforced structurally by these
functions simply never reading those fields, and pinned by
tests/architecture/test_margin_never_in_rendered_document.py.
"""

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.i18n import SwissLanguage
from app.platform.public import (
    Addressee,
    ContentDefinition,
    DocumentLine,
    KeyValueBlock,
    KeyValueRow,
    LineItemsBlock,
    LineStyle,
    ParagraphBlock,
    render_document,
)
from app.sales.models.contract import SalesContract
from app.sales.models.document import DocumentOwnerType, SalesDocument
from app.sales.models.offer import SalesOffer

_DEFAULT_LANGUAGE = SwissLanguage.DE


def _vehicle_block(vehicle_label: str | None) -> KeyValueBlock:
    return KeyValueBlock(
        heading="Fahrzeug",
        boxed=True,
        rows=[KeyValueRow(label="Fahrzeug", value=vehicle_label or "—")],
    )


def _price_build_up_lines(
    *,
    base_price: Decimal | None,
    options_total: Decimal | None,
    list_price: Decimal | None,
    accessories_total: Decimal | None,
    discount_amount: Decimal | None,
    gross_price: Decimal | None,
    trade_in_value: Decimal | None,
    payable: Decimal | None,
) -> list[DocumentLine]:
    """The confirmed live Preisaufbau tab's own line order — base -> options
    -> list -> accessories -> discount -> price -> trade-in -> payable.
    NEVER margin, cost_basis, or trade_in_purchase_price (see module
    docstring).
    """

    lines: list[DocumentLine] = []
    if base_price is not None:
        lines.append(DocumentLine(label="Grundpreis", amount=base_price))
    if options_total is not None and options_total > 0:
        lines.append(DocumentLine(label="Optionen total", amount=options_total, style=LineStyle.SUB))
    if list_price is not None:
        lines.append(DocumentLine(label="Listenpreis", amount=list_price, style=LineStyle.TOTAL))
    if accessories_total is not None and accessories_total > 0:
        lines.append(DocumentLine(label="Zubehör", amount=accessories_total, style=LineStyle.SUB))
    if discount_amount is not None and discount_amount > 0:
        lines.append(DocumentLine(label="Rabatt", amount=-discount_amount))
    if gross_price is not None:
        lines.append(DocumentLine(label="Verkaufspreis", amount=gross_price, style=LineStyle.GRAND))
    if trade_in_value is not None:
        lines.append(DocumentLine(label="Eintauschfahrzeug", amount=-trade_in_value))
    if payable is not None:
        lines.append(DocumentLine(label="Zu bezahlen", amount=payable, style=LineStyle.GRAND))
    return lines


def build_offer_content(offer: SalesOffer) -> ContentDefinition:
    return ContentDefinition(
        title=f"Offerte {offer.offer_number}",
        metadata=[KeyValueRow(label="Datum", value=utcnow().date().isoformat())],
        addressee=Addressee(lines=[offer.customer_label or "—"]),
        blocks=[
            ParagraphBlock(text="Wir freuen uns, Ihnen folgendes Fahrzeug anzubieten."),
            _vehicle_block(offer.vehicle_label),
            LineItemsBlock(
                lines=_price_build_up_lines(
                    base_price=offer.base_price,
                    options_total=offer.options_total,
                    list_price=offer.list_price,
                    accessories_total=offer.accessories_total,
                    discount_amount=offer.discount_amount,
                    gross_price=offer.gross_price,
                    trade_in_value=offer.trade_in_value,
                    payable=offer.payable,
                )
            ),
        ],
    )


def build_contract_content(contract: SalesContract) -> ContentDefinition:
    return ContentDefinition(
        title=f"Kaufvertrag {contract.contract_number}",
        metadata=[KeyValueRow(label="Datum", value=utcnow().date().isoformat())],
        addressee=Addressee(lines=[contract.customer_label or "—"]),
        blocks=[
            _vehicle_block(contract.vehicle_label),
            LineItemsBlock(
                lines=_price_build_up_lines(
                    base_price=contract.base_price,
                    options_total=contract.options_total,
                    list_price=contract.list_price,
                    accessories_total=contract.accessories_total,
                    discount_amount=contract.discount_amount,
                    gross_price=contract.gross_price,
                    trade_in_value=contract.trade_in_value,
                    payable=contract.payable,
                )
            ),
        ],
    )


def _next_version(db: Session, *, tenant_id: uuid.UUID, owner_type: DocumentOwnerType, owner_id: uuid.UUID) -> int:
    current_max = db.scalar(
        select(func.max(SalesDocument.version)).where(
            SalesDocument.tenant_id == tenant_id,
            SalesDocument.owner_type == owner_type,
            SalesDocument.owner_id == owner_id,
        )
    )
    return (current_max or 0) + 1


def generate_offer_document(db: Session, *, offer: SalesOffer, actor_id: uuid.UUID | None) -> SalesDocument:
    content = build_offer_content(offer)
    document = SalesDocument(
        tenant_id=offer.tenant_id,
        owner_type=DocumentOwnerType.OFFER,
        owner_id=offer.id,
        version=_next_version(db, tenant_id=offer.tenant_id, owner_type=DocumentOwnerType.OFFER, owner_id=offer.id),
        correspondence_language=offer.customer_language or _DEFAULT_LANGUAGE.value,
        content_definition=content.model_dump(mode="json"),
        rendered_at=utcnow(),
        rendered_by=actor_id,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def generate_contract_document(db: Session, *, contract: SalesContract, actor_id: uuid.UUID | None) -> SalesDocument:
    content = build_contract_content(contract)
    document = SalesDocument(
        tenant_id=contract.tenant_id,
        owner_type=DocumentOwnerType.CONTRACT,
        owner_id=contract.id,
        version=_next_version(
            db, tenant_id=contract.tenant_id, owner_type=DocumentOwnerType.CONTRACT, owner_id=contract.id
        ),
        correspondence_language=contract.customer_language or _DEFAULT_LANGUAGE.value,
        content_definition=content.model_dump(mode="json"),
        rendered_at=utcnow(),
        rendered_by=actor_id,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def list_documents(
    db: Session, *, tenant_id: uuid.UUID, owner_type: DocumentOwnerType, owner_id: uuid.UUID
) -> list[SalesDocument]:
    return list(
        db.scalars(
            select(SalesDocument)
            .where(
                SalesDocument.tenant_id == tenant_id,
                SalesDocument.owner_type == owner_type,
                SalesDocument.owner_id == owner_id,
            )
            .order_by(SalesDocument.version.desc())
        ).all()
    )


def count_documents(db: Session, *, tenant_id: uuid.UUID, owner_type: DocumentOwnerType, owner_id: uuid.UUID) -> int:
    """ADR-060: the grid's own `documents` column is a COUNT, not a list."""

    return (
        db.scalar(
            select(func.count()).where(
                SalesDocument.tenant_id == tenant_id,
                SalesDocument.owner_type == owner_type,
                SalesDocument.owner_id == owner_id,
            )
        )
        or 0
    )


def get_document_or_404(db: Session, *, tenant_id: uuid.UUID, document_id: uuid.UUID) -> SalesDocument:
    from app.core.errors import NotFoundError

    document = db.scalar(
        select(SalesDocument).where(SalesDocument.id == document_id, SalesDocument.tenant_id == tenant_id)
    )
    if document is None:
        raise NotFoundError(f"Document {document_id} was not found.")
    return document


def render_document_pdf(db: Session, *, document: SalesDocument, dealership_id: uuid.UUID) -> bytes:
    """Deterministic re-render from the FROZEN content_definition — never
    from live offer/contract data. A reprint after the dealership changes
    its own letterhead is not byte-identical (stationery is current); the
    content itself never changes (Open Item O-6).
    """

    content = ContentDefinition.model_validate(document.content_definition)
    language = SwissLanguage(document.correspondence_language)
    return render_document(db, dealership_id=dealership_id, correspondence_language=language, content=content)
