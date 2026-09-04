"""The offer/contract PRINTED-DOCUMENT vocabulary (KAN-23, WP-8, ADR-044).

ADR-044's tier decision, made explicit: these ~14 strings are UI chrome —
fixed, identical for every dealership, never admin-editable — not
canonical reference data (the `reference_list`/`reference_value` DB
tables) and not per-dealership branding
(`app.platform.models.document.DocumentTemplate.header_note_*`/
`footer_text_*`, which a dealer DOES customize per language). UI chrome
lives in the code repository as static data, same tier the frontend's own
`frontend/apps/dms/src/i18n/locales/{de,en,fr,it}.json` already occupies
— this is that same convention's backend, sales-owned counterpart, not a
new pattern.

Consumed by app.sales.services.document at GENERATION time (never at
render time) — the customer's correspondence language, never the
generating seller's own UI language, and the result is frozen into
SalesDocument.content_definition permanently (ADR-041's own "frozen
once" posture, applied here to document TEXT rather than a vehicle
snapshot). A later change to this file never alters an already-generated
document.
"""

from app.core.i18n import SwissLanguage

DocumentStrings = dict[str, str]

_STRINGS: dict[SwissLanguage, DocumentStrings] = {
    SwissLanguage.DE: {
        "offer.title": "Offerte {number}",
        "contract.title": "Kaufvertrag {number}",
        "date": "Datum",
        "offer.greeting": "Wir freuen uns, Ihnen folgendes Fahrzeug anzubieten.",
        "vehicle.heading": "Fahrzeug",
        "priceBuildUp.basePrice": "Grundpreis",
        "priceBuildUp.optionsTotal": "Optionen total",
        "priceBuildUp.listPrice": "Listenpreis",
        "priceBuildUp.accessoriesTotal": "Zubehör",
        "priceBuildUp.discountAmount": "Rabatt",
        "priceBuildUp.grossPrice": "Verkaufspreis",
        "priceBuildUp.tradeInValue": "Eintauschfahrzeug",
        "priceBuildUp.payable": "Zu bezahlen",
        # {rate} is the dealership's own dealer_settings.vat_rate,
        # formatted by the caller — never a second source of truth for it.
        "priceBuildUp.includedVat": "Enthaltene MWST ({rate}%)",
    },
    SwissLanguage.FR: {
        "offer.title": "Offre {number}",
        "contract.title": "Contrat de vente {number}",
        "date": "Date",
        "offer.greeting": "Nous avons le plaisir de vous proposer le véhicule suivant.",
        "vehicle.heading": "Véhicule",
        "priceBuildUp.basePrice": "Prix de base",
        "priceBuildUp.optionsTotal": "Total des options",
        "priceBuildUp.listPrice": "Prix catalogue",
        "priceBuildUp.accessoriesTotal": "Accessoires",
        "priceBuildUp.discountAmount": "Remise",
        "priceBuildUp.grossPrice": "Prix de vente",
        "priceBuildUp.tradeInValue": "Véhicule de reprise",
        "priceBuildUp.payable": "Montant à payer",
        "priceBuildUp.includedVat": "TVA incluse ({rate}%)",
    },
    SwissLanguage.IT: {
        "offer.title": "Offerta {number}",
        "contract.title": "Contratto di vendita {number}",
        "date": "Data",
        "offer.greeting": "Siamo lieti di proporLe il seguente veicolo.",
        "vehicle.heading": "Veicolo",
        "priceBuildUp.basePrice": "Prezzo base",
        "priceBuildUp.optionsTotal": "Totale opzioni",
        "priceBuildUp.listPrice": "Prezzo di listino",
        "priceBuildUp.accessoriesTotal": "Accessori",
        "priceBuildUp.discountAmount": "Sconto",
        "priceBuildUp.grossPrice": "Prezzo di vendita",
        "priceBuildUp.tradeInValue": "Veicolo permutato",
        "priceBuildUp.payable": "Importo da pagare",
        "priceBuildUp.includedVat": "IVA inclusa ({rate}%)",
    },
    SwissLanguage.EN: {
        "offer.title": "Offer {number}",
        "contract.title": "Purchase contract {number}",
        "date": "Date",
        "offer.greeting": "We are pleased to offer you the following vehicle.",
        "vehicle.heading": "Vehicle",
        "priceBuildUp.basePrice": "Base price",
        "priceBuildUp.optionsTotal": "Options total",
        "priceBuildUp.listPrice": "List price",
        "priceBuildUp.accessoriesTotal": "Accessories",
        "priceBuildUp.discountAmount": "Discount",
        "priceBuildUp.grossPrice": "Sale price",
        "priceBuildUp.tradeInValue": "Trade-in vehicle",
        "priceBuildUp.payable": "Amount payable",
        "priceBuildUp.includedVat": "VAT included ({rate}%)",
    },
}

# Every language must define exactly the same key set — a missing key is
# a build-time bug (this module's own test asserts it), never a runtime
# fallback to German (CLAUDE.md's own i18n rule: "a missing key renders a
# loud marker and never a German fallback" — for a document that goes to
# a customer, the loud marker IS the ValueError from t() below, since
# there is no on-screen UI to render a marker into).
_KEYS = set(_STRINGS[SwissLanguage.DE])


def t(language: SwissLanguage, key: str, **kwargs: str) -> str:
    """Look up one document string, formatted with kwargs (e.g.
    `number=offer.offer_number`, `rate=formatted_vat_rate`). Raises,
    never falls back — a document generated with a missing key is a bug
    to fix before shipping, not a document to send with a placeholder in
    it.
    """

    try:
        template = _STRINGS[language][key]
    except KeyError as exc:
        raise KeyError(f"No document string {key!r} for language {language.value!r}") from exc
    return template.format(**kwargs)
