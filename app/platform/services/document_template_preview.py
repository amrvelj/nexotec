"""The template preview's sample documents (KAN-206; Dealer Administration
PRD, FR-A-06: "a live preview with real data on every change").

The preview is a real render through the one `render_document` (ADR-051 —
never a second renderer): the Betrieb's own letterhead, logo, envelope
window, footer columns from its master data — its real IBAN among them,
which is why only those who may edit the template may call it — around a
sample offer or invoice in the Betrieb's correspondence language, with
«MUSTER» across the page. The sample's words are content, so they live here
in the four languages, never in the UI bundles. The sample offer carries the
caller's own signature line, so a manager sees how their signature image
prints.

No markup here: what the page looks like is `document_render.py`'s alone.
"""

import datetime as dt
import enum
import uuid
from decimal import Decimal

from app.core.base import utcnow
from app.core.i18n import SwissLanguage, format_date_ch
from app.platform.schemas.document_content import (
    Addressee,
    ContentDefinition,
    DocumentLine,
    KeyValueBlock,
    KeyValueRow,
    LineItemsBlock,
    LineStyle,
    ParagraphBlock,
    SignatureBlock,
)


class PreviewDocument(str, enum.Enum):
    OFFER = "offer"
    INVOICE = "invoice"


_WORDS: dict[SwissLanguage, dict[str, str]] = {
    SwissLanguage.DE: {
        "offer.title": "Offerte 2026-0001",
        "invoice.title": "Rechnung 2026-0001",
        "date": "Datum",
        "dueDate": "Zahlbar bis",
        "vehicle": "Fahrzeug",
        "vehicleSample": "Muster Kompakt 1.5 Automat",
        "greeting": "Sehr geehrter Herr Muster\n\nGerne unterbreiten wir Ihnen unser Angebot.",
        "invoiceText": "Wir danken Ihnen für Ihren Auftrag und stellen Ihnen in Rechnung:",
        "price": "Verkaufspreis",
        "total": "Total",
        "seller": "Verkäufer",
        "addressee": "Herr\nMax Muster\nMusterstrasse 1\n8000 Zürich",
    },
    SwissLanguage.FR: {
        "offer.title": "Offre 2026-0001",
        "invoice.title": "Facture 2026-0001",
        "date": "Date",
        "dueDate": "Payable jusqu'au",
        "vehicle": "Véhicule",
        "vehicleSample": "Modèle compact 1.5 automatique",
        "greeting": "Monsieur,\n\nNous avons le plaisir de vous soumettre notre offre.",
        "invoiceText": "Nous vous remercions de votre commande et vous facturons :",
        "price": "Prix de vente",
        "total": "Total",
        "seller": "Vendeur",
        "addressee": "Monsieur\nMax Exemple\nRue Exemple 1\n1000 Lausanne",
    },
    SwissLanguage.IT: {
        "offer.title": "Offerta 2026-0001",
        "invoice.title": "Fattura 2026-0001",
        "date": "Data",
        "dueDate": "Pagabile entro il",
        "vehicle": "Veicolo",
        "vehicleSample": "Modello compatto 1.5 automatico",
        "greeting": "Egregio Signor Esempio\n\nSiamo lieti di sottoporle la nostra offerta.",
        "invoiceText": "La ringraziamo per il suo ordine e le fatturiamo:",
        "price": "Prezzo di vendita",
        "total": "Totale",
        "seller": "Venditore",
        "addressee": "Signor\nMax Esempio\nVia Esempio 1\n6900 Lugano",
    },
    SwissLanguage.EN: {
        "offer.title": "Offer 2026-0001",
        "invoice.title": "Invoice 2026-0001",
        "date": "Date",
        "dueDate": "Payable by",
        "vehicle": "Vehicle",
        "vehicleSample": "Sample compact 1.5 automatic",
        "greeting": "Dear Mr Sample\n\nWe are pleased to submit our offer.",
        "invoiceText": "Thank you for your order. We invoice you as follows:",
        "price": "Sale price",
        "total": "Total",
        "seller": "Seller",
        "addressee": "Mr\nMax Sample\nSample Street 1\n8000 Zurich",
    },
}

_SAMPLE_PRICE = Decimal("36000.00")
_PAYMENT_DAYS = 30


def sample_content(
    document: PreviewDocument, *, language: SwissLanguage, signer_user_id: uuid.UUID | None
) -> ContentDefinition:
    words = _WORDS[language]
    today = utcnow().date()
    addressee = Addressee(lines=words["addressee"].split("\n"))
    vehicle = KeyValueBlock(
        heading=words["vehicle"], boxed=True, rows=[KeyValueRow(label=words["vehicle"], value=words["vehicleSample"])]
    )
    if document is PreviewDocument.INVOICE:
        due = today + dt.timedelta(days=_PAYMENT_DAYS)
        return ContentDefinition(
            title=words["invoice.title"],
            metadata=[
                KeyValueRow(label=words["date"], value=format_date_ch(today)),
                KeyValueRow(label=words["dueDate"], value=format_date_ch(due)),
            ],
            addressee=addressee,
            blocks=[
                ParagraphBlock(text=words["invoiceText"]),
                vehicle,
                LineItemsBlock(lines=[DocumentLine(label=words["total"], amount=_SAMPLE_PRICE, style=LineStyle.GRAND)]),
            ],
        )
    return ContentDefinition(
        title=words["offer.title"],
        metadata=[KeyValueRow(label=words["date"], value=format_date_ch(today))],
        addressee=addressee,
        blocks=[
            ParagraphBlock(text=words["greeting"]),
            vehicle,
            LineItemsBlock(lines=[DocumentLine(label=words["price"], amount=_SAMPLE_PRICE, style=LineStyle.GRAND)]),
            SignatureBlock(labels=[words["seller"]], signer_user_ids=[signer_user_id]),
        ],
    )
