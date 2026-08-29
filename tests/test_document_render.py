"""WP-6b PR-3: the render engine.

Most tests exercise `_compose_html` directly — the pure HTML-composition
function — so they run in the fast lane without needing WeasyPrint's
Pango/GLib system libraries installed at all. Exactly one test
(`test_render_document_produces_a_real_pdf`) calls the real
`render_document()` end to end and asserts on the actual PDF bytes.
"""

from decimal import Decimal

from app.core.i18n import SwissLanguage
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType, Location
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
from app.platform.services.document_render import _compose_html, render_document


def _make_dealership(db_session, **overrides) -> Dealership:
    group = DealerGroup(name="Test group")
    db_session.add(group)
    db_session.flush()
    defaults = {
        "dealer_group_id": group.id,
        "legal_name": "Garage Muster AG",
        "dealer_license_number": "ZH-1",
        "license_state": "ZH",
        "franchise_type": FranchiseType.INDEPENDENT,
        "address_street": "Bahnhofstrasse",
        "address_house_number": "1",
        "address_postal_code": "8001",
        "address_locality": "Zürich",
        "address_canton": "ZH",
        "phone": "+41441234567",
        "tax_id": "CHE-123.456.789",
    }
    defaults.update(overrides)
    dealership = Dealership(**defaults)
    db_session.add(dealership)
    db_session.flush()
    db_session.refresh(dealership)
    return dealership


def _sample_content() -> ContentDefinition:
    return ContentDefinition(
        title="Sample document",
        metadata=[KeyValueRow(label="Date", value="08.08.2026")],
        addressee=Addressee(lines=["Maria Muster", "Seestrasse 4", "8002 Zürich"]),
        blocks=[
            ParagraphBlock(text="Dear customer, please find your document below."),
            KeyValueBlock(
                heading="Vehicle",
                boxed=True,
                rows=[KeyValueRow(label="VIN", value="WBA12345678901234")],
            ),
            LineItemsBlock(
                lines=[
                    DocumentLine(label="Base price", amount=Decimal(42000)),
                    DocumentLine(label="Options", amount=Decimal(1500), style=LineStyle.SUB),
                    DocumentLine(label="Total", amount=Decimal(43500), style=LineStyle.GRAND),
                ]
            ),
            SignatureBlock(labels=["Buyer", "Seller"]),
        ],
    )


def test_compose_html_includes_the_letterhead(db_session):
    dealership = _make_dealership(db_session)
    html_out = _compose_html(
        dealership=dealership,
        template=None,
        location=None,
        correspondence_language=SwissLanguage.DE,
        content=_sample_content(),
    )
    assert "Garage Muster AG" in html_out
    assert "Bahnhofstrasse 1" in html_out
    assert "Sample document" in html_out


def test_compose_html_tolerates_no_template_row(db_session):
    dealership = _make_dealership(db_session)
    html_out = _compose_html(
        dealership=dealership,
        template=None,
        location=None,
        correspondence_language=SwissLanguage.FR,
        content=_sample_content(),
    )
    # No exception, and no leftover "None" text where boilerplate would go.
    assert "None" not in html_out


def test_compose_html_uses_the_template_boilerplate_in_the_requested_language(db_session):
    dealership = _make_dealership(db_session)
    template = DocumentTemplate(
        dealership_id=dealership.id,
        footer_text_de="Vielen Dank für Ihr Vertrauen.",
        footer_text_fr="Merci de votre confiance.",
        version=1,
    )
    db_session.add(template)
    db_session.flush()

    html_de = _compose_html(
        dealership=dealership, template=template, location=None,
        correspondence_language=SwissLanguage.DE, content=_sample_content(),
    )
    html_fr = _compose_html(
        dealership=dealership, template=template, location=None,
        correspondence_language=SwissLanguage.FR, content=_sample_content(),
    )

    assert "Vielen Dank für Ihr Vertrauen." in html_de
    assert "Merci de votre confiance." not in html_de
    assert "Merci de votre confiance." in html_fr
    assert "Vielen Dank für Ihr Vertrauen." not in html_fr


def test_compose_html_all_four_line_styles_render_distinct_markup(db_session):
    dealership = _make_dealership(db_session)
    content = ContentDefinition(
        title="Lines",
        blocks=[
            LineItemsBlock(
                lines=[
                    DocumentLine(label="a", amount=Decimal(1), style=LineStyle.NORMAL),
                    DocumentLine(label="b", amount=Decimal(2), style=LineStyle.SUB),
                    DocumentLine(label="c", amount=Decimal(3), style=LineStyle.TOTAL),
                    DocumentLine(label="d", amount=Decimal(4), style=LineStyle.GRAND),
                ]
            )
        ],
    )
    html_out = _compose_html(
        dealership=dealership, template=None, location=None,
        correspondence_language=SwissLanguage.EN, content=content,
    )
    assert 'class="doc-line normal"' in html_out
    assert 'class="doc-line sub"' in html_out
    assert 'class="doc-line total"' in html_out
    assert 'class="doc-line grand"' in html_out


def test_compose_html_formats_amounts_the_swiss_way(db_session):
    dealership = _make_dealership(db_session)
    content = ContentDefinition(
        title="Amount",
        blocks=[LineItemsBlock(lines=[DocumentLine(label="Total", amount=Decimal(12500))])],
    )
    html_out = _compose_html(
        dealership=dealership, template=None, location=None,
        correspondence_language=SwissLanguage.DE, content=content,
    )
    assert "CHF 12'500.00" in html_out


def test_compose_html_uses_the_location_address_in_the_footer_when_given(db_session):
    dealership = _make_dealership(db_session)
    location = Location(
        tenant_id=dealership.id,
        name="Filiale Winterthur",
        address_street="Industriestrasse",
        address_house_number="24",
        address_postal_code="8400",
        address_locality="Winterthur",
    )
    db_session.add(location)
    db_session.flush()

    html_with_location = _compose_html(
        dealership=dealership, template=None, location=location,
        correspondence_language=SwissLanguage.DE, content=_sample_content(),
    )
    html_without_location = _compose_html(
        dealership=dealership, template=None, location=None,
        correspondence_language=SwissLanguage.DE, content=_sample_content(),
    )

    assert "Filiale Winterthur" in html_with_location
    assert "Industriestrasse 24" in html_with_location
    assert "Filiale Winterthur" not in html_without_location


def test_content_is_html_escaped(db_session):
    dealership = _make_dealership(db_session)
    content = ContentDefinition(title="<script>alert(1)</script>")
    html_out = _compose_html(
        dealership=dealership, template=None, location=None,
        correspondence_language=SwissLanguage.DE, content=content,
    )
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_render_document_produces_a_real_pdf(db_session):
    """The one test in this package that actually calls WeasyPrint."""
    dealership = _make_dealership(db_session)
    pdf_bytes = render_document(
        db_session,
        dealership_id=dealership.id,
        correspondence_language=SwissLanguage.DE,
        content=_sample_content(),
    )
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 1000
