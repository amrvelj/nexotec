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

KAN-23: every label is resolved through app.sales.i18n.t() at GENERATION
time, in the CUSTOMER's correspondence language (offer.customer_language/
contract.customer_language), never the generating seller's own UI
language — passed explicitly into build_offer_content/build_contract_
content rather than read from ambient context, which is what makes
"wrong language on the document" structurally impossible (CLAUDE.md's own
rule). The result is frozen into content_definition once; a later change
to app.sales.i18n never alters an already-generated document (ADR-041's
posture, applied to text).

ADR-057: exactly one VAT line, computed at the dealership's own
dealer_settings.vat_rate — never a second source of truth, never a net
price, never a rate breakdown across multiple lines.
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal

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
    get_dealership_or_404,
    render_document,
)
from app.sales.i18n import t
from app.sales.models.contract import SalesContract
from app.sales.models.document import DocumentOwnerType, SalesDocument
from app.sales.models.offer import SalesOffer

_DEFAULT_LANGUAGE = SwissLanguage.DE


def _vehicle_block(vehicle_label: str | None, *, language: SwissLanguage) -> KeyValueBlock:
    heading = t(language, "vehicle.heading")
    return KeyValueBlock(
        heading=heading,
        boxed=True,
        rows=[KeyValueRow(label=heading, value=vehicle_label or "—")],
    )


def _compute_vat_amount(*, gross_price: Decimal, vat_rate: Decimal) -> Decimal:
    """gross_price already INCLUDES VAT (ADR-057 — one gross price, no net
    line) — this is the VAT component OF it, the same reverse-inclusive
    formula app.inventory.services.purchase::_compute_notional_input_tax
    uses for the fiktiver Vorsteuerabzug (a different figure, same math):
    amount = rate / (100 + rate) * gross_price.
    """

    return (gross_price * vat_rate / (Decimal(100) + vat_rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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
    vat_rate: Decimal | None,
    language: SwissLanguage,
) -> list[DocumentLine]:
    """The confirmed live Preisaufbau tab's own line order — base -> options
    -> list -> accessories -> discount -> price -> trade-in -> payable ->
    (KAN-23) included VAT. NEVER margin, cost_basis, or
    trade_in_purchase_price (see module docstring).
    """

    lines: list[DocumentLine] = []
    if base_price is not None:
        lines.append(DocumentLine(label=t(language, "priceBuildUp.basePrice"), amount=base_price))
    if options_total is not None and options_total > 0:
        lines.append(
            DocumentLine(label=t(language, "priceBuildUp.optionsTotal"), amount=options_total, style=LineStyle.SUB)
        )
    if list_price is not None:
        lines.append(
            DocumentLine(label=t(language, "priceBuildUp.listPrice"), amount=list_price, style=LineStyle.TOTAL)
        )
    if accessories_total is not None and accessories_total > 0:
        lines.append(
            DocumentLine(
                label=t(language, "priceBuildUp.accessoriesTotal"), amount=accessories_total, style=LineStyle.SUB
            )
        )
    if discount_amount is not None and discount_amount > 0:
        lines.append(DocumentLine(label=t(language, "priceBuildUp.discountAmount"), amount=-discount_amount))
    if gross_price is not None:
        lines.append(
            DocumentLine(label=t(language, "priceBuildUp.grossPrice"), amount=gross_price, style=LineStyle.GRAND)
        )
    if trade_in_value is not None:
        lines.append(DocumentLine(label=t(language, "priceBuildUp.tradeInValue"), amount=-trade_in_value))
    if payable is not None:
        lines.append(
            DocumentLine(label=t(language, "priceBuildUp.payable"), amount=payable, style=LineStyle.GRAND)
        )
    # ADR-057: exactly one VAT line, informational — never a second price,
    # never added to or subtracted from the totals above. Needs BOTH a
    # price to compute against and a configured rate; either missing
    # means no line at all, never a guessed rate.
    vat_base = payable if payable is not None else gross_price
    if vat_base is not None and vat_rate is not None:
        vat_amount = _compute_vat_amount(gross_price=vat_base, vat_rate=vat_rate)
        rate_label = str(vat_rate.normalize())
        lines.append(
            DocumentLine(
                label=t(language, "priceBuildUp.includedVat", rate=rate_label), amount=vat_amount, style=LineStyle.SUB
            )
        )
    return lines


def build_offer_content(offer: SalesOffer, *, language: SwissLanguage, vat_rate: Decimal | None) -> ContentDefinition:
    """`language` is the CUSTOMER's correspondence language (offer.
    customer_language), passed explicitly by the caller — never read from
    ambient/UI-locale context, which is what makes "wrong language on the
    document" structurally impossible. `vat_rate` is the dealership's own
    dealer_settings.vat_rate at generation time, also passed explicitly
    (this function does no lookup of its own) so a later rate change
    never alters an already-generated document.
    """

    return ContentDefinition(
        title=t(language, "offer.title", number=offer.offer_number),
        metadata=[KeyValueRow(label=t(language, "date"), value=utcnow().date().isoformat())],
        addressee=Addressee(lines=[offer.customer_label or "—"]),
        blocks=[
            ParagraphBlock(text=t(language, "offer.greeting")),
            _vehicle_block(offer.vehicle_label, language=language),
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
                    vat_rate=vat_rate,
                    language=language,
                )
            ),
        ],
    )


def build_contract_content(
    contract: SalesContract, *, language: SwissLanguage, vat_rate: Decimal | None
) -> ContentDefinition:
    """See build_offer_content's own docstring — same explicit-language,
    explicit-rate posture.
    """

    return ContentDefinition(
        title=t(language, "contract.title", number=contract.contract_number),
        metadata=[KeyValueRow(label=t(language, "date"), value=utcnow().date().isoformat())],
        addressee=Addressee(lines=[contract.customer_label or "—"]),
        blocks=[
            _vehicle_block(contract.vehicle_label, language=language),
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
                    vat_rate=vat_rate,
                    language=language,
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
    language = SwissLanguage(offer.customer_language) if offer.customer_language else _DEFAULT_LANGUAGE
    dealership = get_dealership_or_404(db, offer.tenant_id)
    content = build_offer_content(offer, language=language, vat_rate=dealership.vat_rate)
    document = SalesDocument(
        tenant_id=offer.tenant_id,
        owner_type=DocumentOwnerType.OFFER,
        owner_id=offer.id,
        version=_next_version(db, tenant_id=offer.tenant_id, owner_type=DocumentOwnerType.OFFER, owner_id=offer.id),
        correspondence_language=language.value,
        content_definition=content.model_dump(mode="json"),
        rendered_at=utcnow(),
        rendered_by=actor_id,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def generate_contract_document(db: Session, *, contract: SalesContract, actor_id: uuid.UUID | None) -> SalesDocument:
    language = SwissLanguage(contract.customer_language) if contract.customer_language else _DEFAULT_LANGUAGE
    dealership = get_dealership_or_404(db, contract.tenant_id)
    content = build_contract_content(contract, language=language, vat_rate=dealership.vat_rate)
    document = SalesDocument(
        tenant_id=contract.tenant_id,
        owner_type=DocumentOwnerType.CONTRACT,
        owner_id=contract.id,
        version=_next_version(
            db, tenant_id=contract.tenant_id, owner_type=DocumentOwnerType.CONTRACT, owner_id=contract.id
        ),
        correspondence_language=language.value,
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
