"""KAN-206: the letterhead and the shared document template (Dealer
Administration PRD, "Letterhead and the document template"; Anto's answers of
2026-11-12).

The envelope window is measured on WeasyPrint's own layout — the box the
addressee is laid out in, in millimetres from the sheet's top-left corner —
against Swiss Post's positions, never against the CSS text. The footer
columns, the watermark, the page numbers, the signature image and the
per-location override are checked on the composed document, read at render
time.
"""

import uuid
from decimal import Decimal

import pytest

from app.core.i18n import SwissLanguage
from app.core.object_store import LocalDirectoryObjectStore
from app.platform.models.document_template import AddressWindow, DocumentTemplate, HeaderLayout, LogoSize
from app.platform.models.user import User, UserRole, UserStatus
from app.platform.organisation.models.branding_asset import BrandingAsset, BrandingAssetKind
from app.platform.organisation.models.dealership import Location
from app.platform.organisation.models.dealership_bank_account import DealershipBankAccount
from app.platform.schemas.document_content import (
    Addressee,
    ContentDefinition,
    DocumentLine,
    DocumentWatermark,
    KeyValueRow,
    LineItemsBlock,
    ParagraphBlock,
    SignatureBlock,
)
from app.platform.services import document_letterhead as letterhead
from app.platform.services.document_render import (
    FOLD_MARK_CLASS,
    WINDOW_CLASS,
    _compose_html,
    compose_document_html,
    layout_document,
)
from tests.test_document_render import _make_dealership

_PX_TO_MM = 25.4 / 96
_TOLERANCE_MM = 0.5
_PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _content(**overrides) -> ContentDefinition:
    values = {
        "title": "Offerte 2026-0001",
        "metadata": [KeyValueRow(label="Datum", value="14.11.2026")],
        "addressee": Addressee(lines=["Herr", "Max Mustermann", "Mustergasse 99", "8888 Musterhausen"]),
        "blocks": [
            ParagraphBlock(text="Gerne unterbreiten wir Ihnen unser Angebot."),
            LineItemsBlock(lines=[DocumentLine(label="Verkaufspreis", amount=Decimal(36000))]),
        ],
    }
    values.update(overrides)
    return ContentDefinition(**values)


def _template(dealership, **fields) -> DocumentTemplate:
    return DocumentTemplate(dealership_id=dealership.id, version=1, **fields)


def _walk(box):
    yield box
    for child in getattr(box, "children", None) or []:
        yield from _walk(child)


def _boxes_with_class(page, css_class: str) -> list:
    found = []
    for box in _walk(page._page_box):
        element = getattr(box, "element", None)
        if element is not None and css_class in (element.get("class") or "").split():
            found.append(box)
    return found


def _outer_box_mm(page, css_class: str) -> tuple[float, float, float, float]:
    boxes = _boxes_with_class(page, css_class)
    assert boxes, f"no .{css_class} laid out on the page"
    box = boxes[0]  # the element's own box, before its line boxes
    return (
        box.position_x * _PX_TO_MM,
        box.position_y * _PX_TO_MM,
        box.border_width() * _PX_TO_MM,
        box.border_height() * _PX_TO_MM,
    )


def _page_text(page) -> str:
    return " ".join(box.text for box in _walk(page._page_box) if hasattr(box, "text"))


# --- the envelope window (Q1 B), measured on the laid-out page -------------------


@pytest.mark.parametrize("window", [AddressWindow.RIGHT, AddressWindow.LEFT])
def test_the_addressee_lands_in_swiss_posts_window(db_session, window):
    dealership = _make_dealership(db_session)
    html_out = _compose_html(
        dealership=dealership,
        template=_template(dealership, address_window=window),
        location=None,
        correspondence_language=SwissLanguage.DE,
        content=_content(),
    )
    first_page = layout_document(html_out).pages[0]

    left, top, width, height = _outer_box_mm(first_page, WINDOW_CLASS)
    expected = letterhead.WINDOW_GEOMETRY[window]
    assert left == pytest.approx(expected.left, abs=_TOLERANCE_MM)
    assert top == pytest.approx(expected.top, abs=_TOLERANCE_MM)
    assert width == pytest.approx(expected.width, abs=_TOLERANCE_MM)
    assert height == pytest.approx(expected.height, abs=_TOLERANCE_MM)

    # Every addressee line is laid out inside the window, none clipped.
    lines = [box for box in _walk(first_page._page_box) if type(box).__name__ == "LineBox"]
    window_lines = [
        box for box in lines
        if box.element is not None and WINDOW_CLASS in (box.element.get("class") or "")
    ]
    assert len(window_lines) == 4
    for line in window_lines:
        assert expected.top - _TOLERANCE_MM <= line.position_y * _PX_TO_MM
        assert (line.position_y + line.height) * _PX_TO_MM <= expected.top + expected.height + _TOLERANCE_MM
        assert expected.left - _TOLERANCE_MM <= line.position_x * _PX_TO_MM


def test_swiss_posts_values_are_the_published_ones():
    """The geometry the test above measures against — Swiss Post's always-
    visible area of its C5 standard window (50 x 100 mm), from the sheet's
    upper left edge. A change here is a change of the source, not a tweak."""

    assert letterhead.WINDOW_GEOMETRY[AddressWindow.RIGHT] == letterhead.WindowGeometry(117.0, 47.0, 79.0, 34.0)
    assert letterhead.WINDOW_GEOMETRY[AddressWindow.LEFT] == letterhead.WindowGeometry(15.0, 47.0, 80.0, 34.0)


def test_page_one_carries_the_fold_mark_and_a_shipped_template_puts_the_window_right(db_session):
    dealership = _make_dealership(db_session)
    html_out = _compose_html(
        dealership=dealership, template=None, location=None,
        correspondence_language=SwissLanguage.DE, content=_content(),
    )
    first_page = layout_document(html_out).pages[0]

    left, top, _width, _height = _outer_box_mm(first_page, WINDOW_CLASS)
    assert (left, top) == pytest.approx((117.0, 47.0), abs=_TOLERANCE_MM)
    fold_left, fold_top, fold_width, _ = _outer_box_mm(first_page, FOLD_MARK_CLASS)
    assert fold_top == pytest.approx(148.5, abs=_TOLERANCE_MM)
    assert fold_left == pytest.approx(5.0, abs=_TOLERANCE_MM)
    assert fold_width == pytest.approx(5.0, abs=_TOLERANCE_MM)


def test_the_body_starts_below_the_window(db_session):
    dealership = _make_dealership(db_session)
    html_out = _compose_html(
        dealership=dealership, template=None, location=None,
        correspondence_language=SwissLanguage.DE, content=_content(),
    )
    first_page = layout_document(html_out).pages[0]
    title = _boxes_with_class(first_page, "doc-title")[0]
    window = letterhead.WINDOW_GEOMETRY[AddressWindow.RIGHT]
    assert title.position_y * _PX_TO_MM >= window.top + window.height


# --- the header layout and the logo size ---------------------------------------------


@pytest.mark.parametrize("layout", list(HeaderLayout))
def test_each_header_layout_composes_its_own_letterhead(db_session, layout):
    dealership = _make_dealership(db_session)
    html_out = _compose_html(
        dealership=dealership, template=_template(dealership, header_layout=layout), location=None,
        correspondence_language=SwissLanguage.DE, content=_content(),
    )
    assert f'class="doc-head {layout.value}"' in html_out
    head = html_out[html_out.index('<div class="doc-head'):]
    logo_at, sender_at = head.index("doc-logo"), head.index("doc-dealer")
    # The logo comes first, except where it sits on the right.
    assert (logo_at > sender_at) == (layout is HeaderLayout.LOGO_RIGHT)
    assert layout_document(html_out).pages  # lays out without error


@pytest.mark.parametrize(("size", "width_mm"), [(LogoSize.S, 30), (LogoSize.M, 40), (LogoSize.L, 50)])
def test_the_logo_prints_at_most_its_sizes_width_and_20_mm_high(db_session, size, width_mm):
    from app.core.object_store import StoredObject

    dealership = _make_dealership(db_session)
    html_out = _compose_html(
        dealership=dealership, template=_template(dealership, logo_size=size), location=None,
        correspondence_language=SwissLanguage.DE, content=_content(),
        logo=StoredObject(data=_PNG_1x1, content_type="image/png"),
    )
    assert f"max-width:{width_mm:.2f}mm" in html_out
    assert "max-height: 20.00mm" in html_out


# --- the footer columns, from master data at render time (A-27) -----------------------


def _footer(html_out: str) -> str:
    return html_out[html_out.index('<div class="doc-footer">'):html_out.index('<div class="doc-head')]


def _account(dealership, **overrides) -> DealershipBankAccount:
    values = {
        "dealership_id": dealership.id,
        "account_holder": dealership.legal_name,
        "bank_name": "Zürcher Kantonalbank",
        "iban": "CH9300762011623852957",
        "bic": "ZKBKCHZZ80A",
        "is_default": True,
    }
    values.update(overrides)
    return DealershipBankAccount(**values)


def test_the_four_footer_columns_print_master_data_in_the_templates_order(db_session):
    dealership = _make_dealership(
        db_session, email="info@garage-muster.ch", website="www.garage-muster.ch",
        hr_number="CH-020.3.123.456-7", commercial_register_office="ZH",
    )
    db_session.add(_account(dealership))
    db_session.flush()

    html_out = compose_document_html(
        db_session, dealership_id=dealership.id, correspondence_language=SwissLanguage.DE, content=_content()
    )
    footer = _footer(html_out)
    order = [footer.index(f"doc-footer-column {code}") for code in ("company", "contact", "banking", "register")]
    assert order == sorted(order)
    assert "Garage Muster AG<br>Bahnhofstrasse 1<br>8001 Zürich" in footer
    assert "Tel. +41 44 123 45 67<br>info@garage-muster.ch<br>www.garage-muster.ch" in footer
    assert "Zürcher Kantonalbank<br>IBAN CH93 0076 2011 6238 5295 7<br>BIC ZKBKCHZZ80A" in footer
    assert "Handelsregister ZH<br>CH-020.3.123.456-7" in footer
    assert f"UID {dealership.uid}" in footer

    db_session.add(
        _template(dealership, footer_columns=["register", "company"])
    )
    db_session.flush()
    reordered = _footer(
        compose_document_html(
            db_session, dealership_id=dealership.id, correspondence_language=SwissLanguage.DE, content=_content()
        )
    )
    assert reordered.index("doc-footer-column register") < reordered.index("doc-footer-column company")
    assert "doc-footer-column banking" not in reordered
    assert "doc-footer-column contact" not in reordered


def test_the_footer_reads_master_data_when_it_renders_never_a_copy(db_session):
    dealership = _make_dealership(db_session, email="alt@garage-muster.ch")
    first = _footer(compose_document_html(
        db_session, dealership_id=dealership.id, correspondence_language=SwissLanguage.DE, content=_content()
    ))
    dealership.email = "neu@garage-muster.ch"
    dealership.vat_liable = True
    dealership.vat_number = f"{dealership.uid} MWST"
    db_session.flush()
    second = _footer(compose_document_html(
        db_session, dealership_id=dealership.id, correspondence_language=SwissLanguage.DE, content=_content()
    ))
    assert "alt@garage-muster.ch" in first and "alt@garage-muster.ch" not in second
    assert "neu@garage-muster.ch" in second
    assert f"{dealership.uid} MWST" in second


def test_a_column_without_data_is_left_out(db_session):
    """No bank account, no e-mail or website: the banking column is not
    printed, the contact column is the phone alone."""

    dealership = _make_dealership(db_session)
    footer = _footer(compose_document_html(
        db_session, dealership_id=dealership.id, correspondence_language=SwissLanguage.DE, content=_content()
    ))
    assert "doc-footer-column banking" not in footer
    assert 'doc-footer-column contact">Tel. +41 44 123 45 67</div>' in footer


def test_a_location_prints_its_own_bank_account_and_nothing_else_of_the_footer_changes(db_session):
    """The ADR-051 addendum: a site's own account in the banking column; the
    company column stays the legal entity's (A-27)."""

    dealership = _make_dealership(db_session)
    default = _account(dealership)
    own = _account(dealership, bank_name="Bank Winterthur", iban="CH5604835012345678009", bic=None, is_default=False)
    db_session.add_all([default, own])
    db_session.flush()
    site = Location(tenant_id=dealership.id, name="Filiale Winterthur", bank_account_override_id=own.id,
                    address_street="Industriestrasse", address_house_number="24",
                    address_postal_code="8400", address_locality="Winterthur")
    plain_site = Location(tenant_id=dealership.id, name="Filiale Baden")
    db_session.add_all([site, plain_site])
    db_session.flush()

    def footer_for(location_id):
        return _footer(compose_document_html(
            db_session, dealership_id=dealership.id, correspondence_language=SwissLanguage.DE,
            content=_content(), location_id=location_id,
        ))

    at_site, at_plain_site, at_betrieb = footer_for(site.id), footer_for(plain_site.id), footer_for(None)
    assert "Bank Winterthur" in at_site and "Zürcher Kantonalbank" not in at_site
    assert "Zürcher Kantonalbank" in at_plain_site and "Zürcher Kantonalbank" in at_betrieb
    for footer in (at_site, at_plain_site, at_betrieb):
        assert "Garage Muster AG<br>Bahnhofstrasse 1<br>8001 Zürich" in footer
        assert "Winterthur" not in footer.replace("Bank Winterthur", "")


@pytest.mark.parametrize(
    ("language", "phone", "uid"),
    [(SwissLanguage.DE, "Tel.", "UID"), (SwissLanguage.FR, "Tél.", "IDE"),
     (SwissLanguage.IT, "Tel.", "IDI"), (SwissLanguage.EN, "Phone", "UID")],
)
def test_the_footer_labels_follow_the_documents_language(db_session, language, phone, uid):
    dealership = _make_dealership(db_session)
    footer = _footer(compose_document_html(
        db_session, dealership_id=dealership.id, correspondence_language=language, content=_content()
    ))
    assert f"{phone} +41 44 123 45 67" in footer
    assert f"{uid} {dealership.uid}" in footer


# --- the watermark and the page numbers ------------------------------------------------


@pytest.mark.parametrize(
    ("language", "draft", "sample"),
    [(SwissLanguage.DE, "ENTWURF", "MUSTER"), (SwissLanguage.FR, "PROJET", "SPÉCIMEN"),
     (SwissLanguage.IT, "BOZZA", "FACSIMILE"), (SwissLanguage.EN, "DRAFT", "SAMPLE")],
)
def test_the_watermark_prints_in_the_documents_language(db_session, language, draft, sample):
    dealership = _make_dealership(db_session)

    def compose(watermark, template=None):
        return _compose_html(dealership=dealership, template=template, location=None,
                             correspondence_language=language, content=_content(), watermark=watermark)

    assert f'<div class="doc-watermark">{draft}</div>' in compose(DocumentWatermark.DRAFT)
    assert f'<div class="doc-watermark">{sample}</div>' in compose(DocumentWatermark.SAMPLE)
    assert '<div class="doc-watermark">' not in compose(None)
    off = _template(dealership, draft_watermark=False)
    # The template's switch turns the draft mark off; the preview's sample stays.
    assert '<div class="doc-watermark">' not in compose(DocumentWatermark.DRAFT, off)
    assert f'<div class="doc-watermark">{sample}</div>' in compose(DocumentWatermark.SAMPLE, off)


@pytest.mark.parametrize(
    ("language", "wording"),
    [(SwissLanguage.DE, "Seite {page} von {pages}"), (SwissLanguage.FR, "Page {page} sur {pages}"),
     (SwissLanguage.IT, "Pagina {page} di {pages}"), (SwissLanguage.EN, "Page {page} of {pages}")],
)
def test_page_numbers_print_in_four_languages(db_session, language, wording):
    dealership = _make_dealership(db_session)
    long_content = _content(blocks=[ParagraphBlock(text="Absatz " * 40) for _ in range(12)])
    html_out = _compose_html(dealership=dealership, template=None, location=None,
                             correspondence_language=language, content=long_content)
    pages = layout_document(html_out).pages
    total = len(pages)
    assert total >= 2
    for number, page in enumerate(pages, start=1):
        assert wording.format(page=number, pages=total) in _page_text(page)

    off = _compose_html(dealership=dealership, template=_template(dealership, page_numbers=False), location=None,
                        correspondence_language=language, content=long_content)
    assert wording.format(page=1, pages=total) not in _page_text(layout_document(off).pages[0])


# --- the signature image (Q2 A), read at render time ----------------------------------


def _user(db_session, tenant_id: uuid.UUID, *, signature_ref: str | None = None) -> User:
    user = User(
        tenant_id=tenant_id, first_name="Vera", last_name="Verkauf", email=f"{uuid.uuid4().hex}@example.test",
        role=UserRole.SALES, access_roles=[], is_dealer_manager=False, status=UserStatus.ACTIVE,
        auth_identity_id=str(uuid.uuid4()), signature_image_ref=signature_ref,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_the_signers_current_signature_image_prints_above_their_line(db_session, tmp_path):
    store = LocalDirectoryObjectStore(tmp_path / "objects")
    dealership = _make_dealership(db_session)
    seller = _user(db_session, dealership.id, signature_ref="sig/seller")
    without = _user(db_session, dealership.id)
    stranger = _user(db_session, uuid.uuid4(), signature_ref="sig/stranger")
    store.put("sig/seller", b"\x89PNG-first", content_type="image/png")
    store.put("sig/stranger", b"\x89PNG-stranger", content_type="image/png")
    content = _content(blocks=[SignatureBlock(
        labels=["Käufer", "Verkäufer", "Ohne", "Fremd"], signer_user_ids=[None, seller.id, without.id, stranger.id]
    )])

    def compose():
        return compose_document_html(db_session, dealership_id=dealership.id,
                                     correspondence_language=SwissLanguage.DE, content=content, store=store)

    import base64

    first = compose()
    assert base64.b64encode(b"\x89PNG-first").decode() in first
    # A user of another Betrieb never prints, even named by the content.
    assert base64.b64encode(b"\x89PNG-stranger").decode() not in first
    assert first.count("<img ") == 1

    # Read at reprint: a replaced image prints the new one.
    store.put("sig/seller", b"\x89PNG-second", content_type="image/png")
    second = compose()
    assert base64.b64encode(b"\x89PNG-second").decode() in second
    assert base64.b64encode(b"\x89PNG-first").decode() not in second


# --- per location: the address block and the logo only --------------------------------


def test_a_location_prints_its_own_logo_else_the_betriebs(db_session, tmp_path):
    import base64

    store = LocalDirectoryObjectStore(tmp_path / "objects")
    dealership = _make_dealership(db_session)
    site = Location(tenant_id=dealership.id, name="Filiale Winterthur")
    plain_site = Location(tenant_id=dealership.id, name="Filiale Baden")
    db_session.add_all([site, plain_site])
    db_session.flush()
    for key, data, location_id in (("logo/betrieb", b"<svg>betrieb</svg>", None), ("logo/site", b"<svg>site</svg>", site.id)):
        store.put(key, data, content_type="image/svg+xml")
        db_session.add(BrandingAsset(dealership_id=dealership.id, location_id=location_id, kind=BrandingAssetKind.PRIMARY,
                                     object_key=key, mime_type="image/svg+xml", byte_size=len(data)))
    db_session.flush()

    def compose(location_id):
        return compose_document_html(db_session, dealership_id=dealership.id, correspondence_language=SwissLanguage.DE,
                                     content=_content(), location_id=location_id, store=store)

    betrieb_logo = base64.b64encode(b"<svg>betrieb</svg>").decode()
    site_logo = base64.b64encode(b"<svg>site</svg>").decode()
    assert site_logo in compose(site.id) and betrieb_logo not in compose(site.id)
    assert betrieb_logo in compose(plain_site.id)
    assert betrieb_logo in compose(None)
