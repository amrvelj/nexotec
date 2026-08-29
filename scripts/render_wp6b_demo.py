"""WP-6b exit-criterion demonstration: "one document definition renders in
all four languages with the correct letterhead for two different
dealerships in the same group, and the French rendering is reviewed for
text expansion."

Self-contained — builds its own throwaway SQLite database (same
bootstrapping tests/conftest.py already uses: env-var defaults set before
any app.* import, app.model_registry to register every table, then
Base.metadata.create_all) rather than requiring a live Postgres server,
since this script exists purely to produce the screenshot deliverable, not
to touch real data.

The sample ContentDefinition below is WP-6b's own exit-criterion
demonstration only — never imported by a real module, and it names no real
document type (see app.platform.schemas.document_content's own docstring
for why this package carries no content vocabulary of its own).

Usage: python scripts/render_wp6b_demo.py [output_dir]
Writes one PDF per (dealership, language) — 8 files — plus a PNG render of
each for quick visual review (requires poppler's pdftoppm on PATH; skipped
with a warning if it isn't).
"""

import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

os.environ.setdefault("DMS_TAX_ID_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault(
    "DMS_JWT_PRIVATE_KEY",
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    .decode("ascii"),
)
os.environ.setdefault("DMS_ZITADEL_ISSUER", "https://example.zitadel.cloud")
os.environ.setdefault("DMS_ZITADEL_CLIENT_ID", "demo-client-id")
os.environ.setdefault("DMS_ZITADEL_CLIENT_SECRET", "demo-client-secret")
os.environ.setdefault("DMS_ZITADEL_REDIRECT_URI", "http://localhost/v1/auth/oidc/callback")
os.environ.setdefault("DMS_SESSION_SECRET_KEY", Fernet.generate_key().decode())

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.model_registry  # noqa: F401  registers every table on Base.metadata
from app.core.i18n import SwissLanguage
from app.db import Base
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.platform.models.document_template import DocumentTemplate
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
from app.platform.services.document_render import render_document

# The one content definition rendered for every (dealership, language) pair —
# demo-only, exercises every block type once. Content strings are supplied
# per language here because THIS SCRIPT plays the role of a future content-
# producing module (WP-7/8/9) — the render layer itself has no vocabulary
# of its own to translate, per its own schema's docstring.
_STRINGS = {
    SwissLanguage.DE: {
        "title": "Offerte O-2026-0042",
        "date_label": "Datum",
        "advisor_label": "Berater",
        "advisor": "Anna Meier",
        "addressee": ["Maria Muster", "Seestrasse 4", "8002 Zürich"],
        "vehicle_heading": "BMW 3er 320d",
        "vin_label": "Fahrgestellnummer",
        "km_label": "Kilometerstand",
        "km": "42'000 km",
        "base_price": "Grundpreis",
        "options": "Metallic-Lackierung",
        "discount": "Rabatt",
        "total": "Verkaufspreis",
        "note": "Alle Preise in CHF inkl. MwSt.",
        "buyer": "Käufer",
        "seller": "Verkäufer",
    },
    SwissLanguage.FR: {
        "title": "Offre O-2026-0042",
        "date_label": "Date",
        "advisor_label": "Conseiller",
        "advisor": "Anna Meier",
        "addressee": ["Maria Muster", "Seestrasse 4", "8002 Zürich"],
        "vehicle_heading": "BMW Série 3 320d",
        "vin_label": "Numéro de châssis",
        "km_label": "Kilométrage",
        "km": "42'000 km",
        "base_price": "Prix de base",
        "options": "Peinture métallisée",
        "discount": "Remise",
        "total": "Prix de vente",
        "note": "Tous les prix en CHF, TVA comprise.",
        "buyer": "Acheteur",
        "seller": "Vendeur",
    },
    SwissLanguage.IT: {
        "title": "Offerta O-2026-0042",
        "date_label": "Data",
        "advisor_label": "Consulente",
        "advisor": "Anna Meier",
        "addressee": ["Maria Muster", "Seestrasse 4", "8002 Zürich"],
        "vehicle_heading": "BMW Serie 3 320d",
        "vin_label": "Numero di telaio",
        "km_label": "Chilometraggio",
        "km": "42'000 km",
        "base_price": "Prezzo base",
        "options": "Verniciatura metallizzata",
        "discount": "Sconto",
        "total": "Prezzo di vendita",
        "note": "Tutti i prezzi in CHF, IVA inclusa.",
        "buyer": "Acquirente",
        "seller": "Venditore",
    },
    SwissLanguage.EN: {
        "title": "Quotation O-2026-0042",
        "date_label": "Date",
        "advisor_label": "Advisor",
        "advisor": "Anna Meier",
        "addressee": ["Maria Muster", "Seestrasse 4", "8002 Zürich"],
        "vehicle_heading": "BMW 3 Series 320d",
        "vin_label": "VIN",
        "km_label": "Odometer",
        "km": "42'000 km",
        "base_price": "Base price",
        "options": "Metallic paint",
        "discount": "Discount",
        "total": "Sale price",
        "note": "All prices in CHF including VAT.",
        "buyer": "Buyer",
        "seller": "Seller",
    },
}


def _content_for(language: SwissLanguage) -> ContentDefinition:
    s = _STRINGS[language]
    return ContentDefinition(
        title=s["title"],
        metadata=[
            KeyValueRow(label=s["date_label"], value="08.08.2026"),
            KeyValueRow(label=s["advisor_label"], value=s["advisor"]),
        ],
        addressee=Addressee(lines=s["addressee"]),
        blocks=[
            KeyValueBlock(
                heading=s["vehicle_heading"],
                boxed=True,
                rows=[
                    KeyValueRow(label=s["vin_label"], value="WBA12345678901234"),
                    KeyValueRow(label=s["km_label"], value=s["km"]),
                ],
            ),
            LineItemsBlock(
                lines=[
                    DocumentLine(label=s["base_price"], amount=Decimal(42000)),
                    DocumentLine(label=s["options"], amount=Decimal(1200), style=LineStyle.SUB),
                    DocumentLine(label=s["discount"], amount=Decimal(-1500)),
                    DocumentLine(label=s["total"], amount=Decimal(41700), style=LineStyle.GRAND),
                ]
            ),
            ParagraphBlock(text=s["note"]),
            SignatureBlock(labels=[s["buyer"], s["seller"]]),
        ],
    )


def _seed_two_dealerships_in_one_group(db: Session) -> list[Dealership]:
    group = DealerGroup(name="Muster Mobility Group")
    db.add(group)
    db.flush()

    zurich = Dealership(
        dealer_group_id=group.id,
        legal_name="Garage Muster AG",
        dealer_license_number="ZH-1",
        license_state="ZH",
        franchise_type=FranchiseType.INDEPENDENT,
        address_street="Bahnhofstrasse",
        address_house_number="1",
        address_postal_code="8001",
        address_locality="Zürich",
        address_canton="ZH",
        phone="+41 44 123 45 67",
        tax_id="CHE-111.111.111",
        default_correspondence_language=SwissLanguage.DE,
    )
    geneva = Dealership(
        dealer_group_id=group.id,
        legal_name="Garage Muster Genève SA",
        dealer_license_number="GE-1",
        license_state="GE",
        franchise_type=FranchiseType.INDEPENDENT,
        address_street="Rue du Rhône",
        address_house_number="10",
        address_postal_code="1204",
        address_locality="Genève",
        address_canton="GE",
        phone="+41 22 123 45 67",
        tax_id="CHE-222.222.222",
        brand_primary_color="#0F766E",
        default_correspondence_language=SwissLanguage.FR,
    )
    db.add_all([zurich, geneva])
    db.flush()

    db.add(
        DocumentTemplate(
            dealership_id=zurich.id,
            footer_text_de="Vielen Dank für Ihr Vertrauen.",
            footer_text_fr="Merci de votre confiance.",
            footer_text_it="Grazie per la fiducia.",
            footer_text_en="Thank you for your trust.",
            version=1,
        )
    )
    db.add(
        DocumentTemplate(
            dealership_id=geneva.id,
            footer_text_de="Vielen Dank für Ihr Vertrauen.",
            footer_text_fr="Merci de votre confiance.",
            footer_text_it="Grazie per la fiducia.",
            footer_text_en="Thank you for your trust.",
            version=1,
        )
    )
    db.commit()
    return [zurich, geneva]


def main() -> None:
    output_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("wp6b_demo_output")
    output_dir.mkdir(parents=True, exist_ok=True)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)

    dealerships = _seed_two_dealerships_in_one_group(db)

    for dealership in dealerships:
        slug = dealership.address_canton.lower()
        for language in SwissLanguage:
            pdf_bytes = render_document(
                db,
                dealership_id=dealership.id,
                correspondence_language=language,
                content=_content_for(language),
            )
            pdf_path = output_dir / f"{slug}_{language.value}.pdf"
            pdf_path.write_bytes(pdf_bytes)
            print(f"wrote {pdf_path}")

            png_path = output_dir / f"{slug}_{language.value}.png"
            try:
                subprocess.run(
                    ["pdftoppm", "-png", "-r", "150", "-singlefile", str(pdf_path), str(png_path.with_suffix(""))],
                    check=True,
                    capture_output=True,
                )
                print(f"wrote {png_path}")
            except (FileNotFoundError, subprocess.CalledProcessError) as exc:
                print(f"skipped PNG for {pdf_path} (pdftoppm unavailable: {exc})")


if __name__ == "__main__":
    main()
