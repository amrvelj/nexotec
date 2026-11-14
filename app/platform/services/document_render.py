"""The shared document-render layer (WP-6b PR-3, ADR-051): the one place in
this application allowed to know what a rendered document looks like.
`tests/architecture/test_no_layout_code_outside_document_render.py` is the
standing check on that — a query, not a code review.

Composing the HTML (`_compose_html`) is kept separate from the actual PDF
call (`render_document`) on purpose: most correctness questions ("is the
letterhead there", "did the footer pick up the right language", "does a
GRAND line double-rule") are about the HTML string, and answering them
doesn't need a real render. The geometry questions — where the addressee
lands on the sheet — are answered against WeasyPrint's own layout
(`layout_document`), never against the CSS text.

KAN-206 (PRD "Letterhead and the document template"): the page is a Swiss
business letter. Page 1 carries the letterhead band (logo left, right or
centred, at the template's size), the addressee in the C5/C4 envelope
window (left or right per Betrieb, Swiss Post's positions), the document's
key facts beside the window, and a fold mark; every page carries the footer
— the document's own footer text above up to four master-data columns read
now (`document_letterhead.footer_columns`; the legal entity always, A-27) —
the page numbers when on, and a DRAFT or SAMPLE watermark when asked. A
document printed for a location takes that site's address block (its
free-text override, Q3 A) and its own logo when it has them; nothing else
changes per site. What is printed and where is decided in
`document_letterhead.py`; this file only lays it out.

Visual values (colours, spacing, radii, fonts) below are copied from
frontend/packages/ui-kit/src/tokens.ts, the canonical, currently-shipped
token source — NOT the interactive prototype's own CSS variables, which
were only the layout reference for how these tokens compose. Keep the two
in sync by hand if either changes; there is no shared build step between a
Python string and a TypeScript module.
"""

import base64
import dataclasses
import html
import re
import uuid
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session
from weasyprint import HTML
from weasyprint.document import Document
from weasyprint.urls import URLFetcher

from app.core.i18n import SwissLanguage, format_currency_chf
from app.core.object_store import ObjectStore, StoredObject, get_object_store
from app.platform.models.document_template import (
    DEFAULT_FOOTER_COLUMNS,
    AddressWindow,
    DocumentTemplate,
    HeaderLayout,
    LogoSize,
)
from app.platform.organisation.models.dealership import Dealership, Location
from app.platform.organisation.models.dealership_bank_account import DealershipBankAccount
from app.platform.organisation.services.branding import document_header_logo
from app.platform.organisation.services.dealership import get_dealership_or_404
from app.platform.organisation.services.location import get_location_or_404
from app.platform.schemas.document_content import (
    Addressee,
    ContentDefinition,
    DocumentWatermark,
    KeyValueBlock,
    KeyValueRow,
    LineItemsBlock,
    ParagraphBlock,
    SignatureBlock,
)
from app.platform.services import document_letterhead as letterhead
from app.platform.services.document_template import get_document_template
from app.platform.services.user_reads import get_user_acting_in

# --- print tokens, copied from frontend/packages/ui-kit/src/tokens.ts ------

_DEFAULT_BRAND_COLOR = "#7C3AED"  # purple[6] — same fallback Dealership's own column default uses
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_SLATE_2 = "#E2E8F0"
_SLATE_5 = "#64748B"
_SLATE_6 = "#475569"
_SLATE_9 = "#0F172A"
_RADIUS_MD = "8px"
_FONT_FAMILY = 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'
_FONT_FAMILY_MONO = "ui-monospace, SF Mono, Menlo, Consolas, monospace"

# The class the addressee's window box carries — what the geometry test
# finds in WeasyPrint's layout.
WINDOW_CLASS = "doc-window"
FOLD_MARK_CLASS = "doc-fold"


def _mm(value: float) -> str:
    return f"{value:.2f}mm"


def _page_style(*, page_numbers: bool, language: SwissLanguage) -> str:
    """The @page rule: A4, the margins the geometry is computed against,
    the running footer, and — when on — «Seite 1 von 3» in the document's
    language, top right on every page."""

    numbers = ""
    if page_numbers:
        head, middle, tail = (_css_string(part) for part in letterhead.page_number_parts(language))
        numbers = (
            f"@top-right {{ content: {head} counter(page) {middle} counter(pages) {tail}; "
            f"font-family: {_FONT_FAMILY}; font-size: 8px; color: {_SLATE_5}; vertical-align: bottom; padding-bottom: 3mm; }}"
        )
    content_width = letterhead.PAGE_WIDTH_MM - letterhead.MARGIN_LEFT_MM - letterhead.MARGIN_RIGHT_MM
    return f"""
@page {{
  size: A4;
  margin: {_mm(letterhead.MARGIN_TOP_MM)} {_mm(letterhead.MARGIN_RIGHT_MM)} {_mm(letterhead.MARGIN_BOTTOM_MM)} {_mm(letterhead.MARGIN_LEFT_MM)};
  @bottom-center {{ content: element(doc-footer); width: {_mm(content_width)}; vertical-align: top; padding-top: 4mm; }}
  {numbers}
}}"""


def _css_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


_STYLE = f"""
body {{ font-family: {_FONT_FAMILY}; font-size: 13px; color: {_SLATE_9}; margin: 0; }}
.doc-footer {{ position: running(doc-footer); font-size: 8px; line-height: 1.45; color: {_SLATE_5}; }}
.doc-footer-text {{ white-space: pre-line; margin-bottom: 2.5mm; }}
.doc-footer-columns {{ display: flex; gap: 6mm; border-top: 1px solid {_SLATE_2}; padding-top: 2mm; }}
.doc-footer-column {{ flex: 1; min-width: 0; }}
.doc-head {{
  height: {_mm(letterhead.HEADER_HEIGHT_MM)}; overflow: hidden; display: flex; gap: 18px;
  align-items: flex-start; justify-content: space-between;
}}
.doc-head.logo_centred {{ flex-direction: column; align-items: center; justify-content: flex-start; gap: 2mm; }}
.doc-dealer {{ font-size: 11px; line-height: 1.5; color: {_SLATE_6}; text-align: right; }}
.doc-head.logo_right .doc-dealer {{ text-align: left; }}
.doc-head.logo_centred .doc-dealer {{ text-align: center; font-size: 9px; line-height: 1.35; }}
.doc-dealer-address {{ white-space: pre-line; }}
.doc-logo {{ flex-shrink: 0; }}
.doc-logo img {{ display: block; max-height: {_mm(letterhead.LOGO_MAX_HEIGHT_MM)}; }}
.doc-logo-mark {{
  width: 14mm; height: 14mm; border-radius: {_RADIUS_MD}; color: #fff; font-size: 22px;
  display: flex; align-items: center; justify-content: center; font-weight: 800;
}}
.{WINDOW_CLASS} {{ position: absolute; overflow: hidden; line-height: 1.45; font-size: 12px; }}
.doc-window-meta {{ position: absolute; font-size: 11px; }}
.doc-band {{ height: {_mm(letterhead.BODY_TOP_MM - letterhead.MARGIN_TOP_MM - letterhead.HEADER_HEIGHT_MM)}; }}
.{FOLD_MARK_CLASS} {{ position: absolute; height: 0; border-top: 0.25mm solid {_SLATE_5}; }}
.doc-watermark {{
  position: fixed; top: 105mm; left: 0; width: 100%; text-align: center; font-size: 96px; font-weight: 800;
  letter-spacing: 6px; color: rgba(15, 23, 42, 0.07); transform: rotate(-35deg);
}}
.doc-meta-row {{ display: flex; gap: 10px; }}
.doc-meta-row span {{ color: {_SLATE_5}; min-width: 28mm; }}
.doc-title {{ font-size: 19px; font-weight: 700; letter-spacing: -0.3px; margin: 0 0 20px; }}
.doc-kv {{ margin-bottom: 20px; }}
.doc-kv.boxed {{ border: 1px solid {_SLATE_2}; border-radius: {_RADIUS_MD}; padding: 12px 14px; }}
.doc-kv-heading {{ font-weight: 700; margin-bottom: 6px; }}
.doc-kv-row {{ display: flex; justify-content: space-between; gap: 8px; font-size: 12px; padding: 2px 0; }}
.doc-kv-row span {{ color: {_SLATE_5}; }}
.doc-kv-row b {{ font-family: {_FONT_FAMILY_MONO}; font-size: 12.5px; }}
.doc-paragraph {{ margin: 14px 0; line-height: 1.6; white-space: pre-line; }}
.doc-lines {{ display: grid; margin: 14px 0; }}
.doc-line {{
  display: flex; justify-content: space-between; gap: 16px; padding: 6px 0;
  border-bottom: 1px solid #F1F5F9;
}}
.doc-line.sub {{ padding-left: 16px; color: {_SLATE_6}; }}
.doc-line.total {{ font-weight: 700; border-bottom: 1px solid #CBD5E1; }}
.doc-line.grand {{ font-size: 16px; border-bottom: 3px double {_SLATE_9}; }}
.doc-line .amount {{ font-family: {_FONT_FAMILY_MONO}; font-size: 12.5px; text-align: right; }}
.doc-sign {{ display: flex; gap: 40px; margin-top: 40px; page-break-inside: avoid; }}
.doc-sign > div {{ flex: 1; }}
.doc-sign-image {{ height: 18mm; display: flex; align-items: flex-end; }}
.doc-sign-image img {{ max-height: 18mm; max-width: 60mm; }}
.doc-sign-line {{ border-top: 1px solid {_SLATE_9}; margin-bottom: 6px; }}
.doc-sign span {{ font-size: 11px; color: {_SLATE_5}; }}
"""


@dataclasses.dataclass(frozen=True)
class Layout:
    """The template's layout as one render reads it — the row's values, or
    the shipped defaults for a Betrieb without a row yet."""

    header_layout: HeaderLayout = HeaderLayout.LOGO_LEFT
    logo_size: LogoSize = LogoSize.M
    address_window: AddressWindow = AddressWindow.RIGHT
    footer_columns: tuple[str, ...] = tuple(column.value for column in DEFAULT_FOOTER_COLUMNS)
    draft_watermark: bool = True
    page_numbers: bool = True

    @classmethod
    def of(cls, template: DocumentTemplate | None) -> "Layout":
        if template is None:
            return cls()
        default = cls()
        # A row not flushed yet holds None where the column default would
        # go: it reads the shipped default, as the database would write it.
        return cls(
            header_layout=HeaderLayout(template.header_layout or default.header_layout),
            logo_size=LogoSize(template.logo_size or default.logo_size),
            address_window=AddressWindow(template.address_window or default.address_window),
            footer_columns=tuple(
                template.footer_columns if template.footer_columns is not None else default.footer_columns
            ),
            draft_watermark=default.draft_watermark if template.draft_watermark is None else template.draft_watermark,
            page_numbers=default.page_numbers if template.page_numbers is None else template.page_numbers,
        )


def _esc(value: str) -> str:
    # quote=False: every call site below places this in HTML TEXT content,
    # never inside an attribute value, so only &/</> need escaping —
    # quote=True would also turn the Swiss apostrophe thousands separator
    # (format_currency_chf) into `&#x27;`, which is wrong here.
    return html.escape(value, quote=False)


def _inline_image(image: StoredObject) -> str:
    inline = base64.b64encode(image.data).decode("ascii")
    return f'<img src="data:{_esc(image.content_type)};base64,{inline}" alt="">'


def _render_key_value_block(block: KeyValueBlock) -> str:
    heading = f'<div class="doc-kv-heading">{_esc(block.heading)}</div>' if block.heading else ""
    rows = "".join(
        f'<div class="doc-kv-row"><span>{_esc(row.label)}</span><b>{_esc(row.value)}</b></div>'
        for row in block.rows
    )
    cls = "doc-kv boxed" if block.boxed else "doc-kv"
    return f'<div class="{cls}">{heading}{rows}</div>'


def _render_paragraph_block(block: ParagraphBlock) -> str:
    return f'<div class="doc-paragraph">{_esc(block.text)}</div>'


def _render_line_items_block(block: LineItemsBlock) -> str:
    lines = "".join(
        f'<div class="doc-line {line.style.value}">'
        f"<span>{_esc(line.label)}</span>"
        f'<span class="amount">{_esc(format_currency_chf(line.amount))}</span>'
        f"</div>"
        for line in block.lines
    )
    return f'<div class="doc-lines">{lines}</div>'


def _render_signature_block(block: SignatureBlock, signatures: Mapping[uuid.UUID, StoredObject]) -> str:
    """One column per line; above a line, the signer's image when the block
    names one and that user has an image now (KAN-206, Q2 A)."""

    signers = [*block.signer_user_ids, *([None] * (len(block.labels) - len(block.signer_user_ids)))]
    columns = []
    for label, signer in zip(block.labels, signers, strict=True):
        image = signatures.get(signer) if signer is not None else None
        image_html = _inline_image(image) if image is not None else ""
        columns.append(
            f'<div><div class="doc-sign-image">{image_html}</div>'
            f'<div class="doc-sign-line"></div><span>{_esc(label)}</span></div>'
        )
    return f'<div class="doc-sign">{"".join(columns)}</div>'


def _render_block(
    block: KeyValueBlock | ParagraphBlock | LineItemsBlock | SignatureBlock,
    signatures: Mapping[uuid.UUID, StoredObject],
) -> str:
    # isinstance dispatch rather than a dict-of-callables keyed by type —
    # each renderer takes a different concrete block type, which a dict of
    # mixed callables can't express cleanly for a type checker.
    if isinstance(block, KeyValueBlock):
        return _render_key_value_block(block)
    if isinstance(block, ParagraphBlock):
        return _render_paragraph_block(block)
    if isinstance(block, LineItemsBlock):
        return _render_line_items_block(block)
    return _render_signature_block(block, signatures)


def _render_window(addressee: Addressee | None, metadata: list[KeyValueRow], window: AddressWindow) -> str:
    """The addressee, absolutely placed in the envelope window (Swiss Post's
    positions, `document_letterhead.WINDOW_GEOMETRY`), the document's key
    facts beside it on the other side, and the fold mark — page 1 only.
    Absolute positions are relative to the page area, so the page margins
    are taken off the sheet coordinates."""

    geometry = letterhead.WINDOW_GEOMETRY[window]
    left = geometry.left - letterhead.MARGIN_LEFT_MM
    top = geometry.top - letterhead.MARGIN_TOP_MM
    lines = "<br>".join(_esc(line) for line in addressee.lines) if addressee is not None else ""
    window_html = (
        f'<div class="{WINDOW_CLASS}" style="left:{_mm(left)};top:{_mm(top)};'
        f'width:{_mm(geometry.width)};height:{_mm(geometry.height)}">{lines}</div>'
    )

    meta_html = ""
    if metadata:
        content_width = letterhead.PAGE_WIDTH_MM - letterhead.MARGIN_LEFT_MM - letterhead.MARGIN_RIGHT_MM
        if window is AddressWindow.RIGHT:
            meta_left, meta_width = 0.0, left - 8.0
        else:
            meta_left = left + geometry.width + 8.0
            meta_width = content_width - meta_left
        rows = "".join(
            f'<div class="doc-meta-row"><span>{_esc(row.label)}</span><b>{_esc(row.value)}</b></div>'
            for row in metadata
        )
        meta_html = (
            f'<div class="doc-window-meta" style="left:{_mm(meta_left)};top:{_mm(top)};width:{_mm(meta_width)}">'
            f"{rows}</div>"
        )

    fold_html = (
        f'<div class="{FOLD_MARK_CLASS}" style="left:{_mm(letterhead.FOLD_MARK_LEFT_MM - letterhead.MARGIN_LEFT_MM)};'
        f"top:{_mm(letterhead.FOLD_MARK_TOP_MM - letterhead.MARGIN_TOP_MM)};"
        f'width:{_mm(letterhead.FOLD_MARK_LENGTH_MM)}"></div>'
    )
    return f'{window_html}{meta_html}{fold_html}<div class="doc-band"></div>'


def _render_logo(dealership: Dealership, logo: StoredObject | None, size: LogoSize = LogoSize.M) -> str:
    """The uploaded logo (KAN-205: the primary branding asset — or, KAN-206,
    the site's own — read from the object store and inlined, so the renderer
    fetches nothing over the network), at most the template's width and 20 mm
    high; otherwise an initials mark in the dealership's own brand colour —
    the same fallback the frontend shell's own BrandMark component uses.

    The WP-6b external `logo_url` is never printed: the renderer fetches
    nothing outside the document (`local_url_fetcher`), so it would only
    print an empty box. scripts/migrate_logo_urls_to_branding_assets.py
    moves it into the store.
    """

    max_width = _mm(letterhead.LOGO_MAX_WIDTH_MM[size])
    if logo is not None:
        inline = base64.b64encode(logo.data).decode("ascii")
        return (
            f'<div class="doc-logo"><img style="max-width:{max_width}" '
            f'src="data:{_esc(logo.content_type)};base64,{inline}" alt=""></div>'
        )
    color = dealership.brand_primary_color
    if not color or not _HEX_COLOR.match(color):
        color = _DEFAULT_BRAND_COLOR
    name = dealership.trade_name or dealership.legal_name
    initial = _esc(name[:1].upper()) if name else ""
    return f'<div class="doc-logo doc-logo-mark" style="background:{color}">{initial}</div>'


def _letterhead_lines(dealership: Dealership, location: Location | None) -> str:
    """The sender block: the Betrieb's name, then its address and phone — or,
    for a site with a letterhead address override, that text as written
    (KAN-206, Q3 A: the free-text override is the site's address block)."""

    name = dealership.trade_name or dealership.legal_name
    override = location.letterhead_address_override if location is not None else None
    if override and override.strip():
        return f"<b>{_esc(name)}</b><br><span class=\"doc-dealer-address\">{_esc(override.strip())}</span>"
    address = dealership.address
    return (
        f"<b>{_esc(name)}</b><br>"
        f"{_esc(address['street'])} {_esc(address['house_number'])} · "
        f"{_esc(address['postal_code'])} {_esc(address['locality'])}<br>"
        f"{_esc(letterhead.format_phone(dealership.phone))}"
    )


def _render_footer(
    dealership: Dealership,
    *,
    footer_text: str | None,
    columns: tuple[str, ...],
    bank_account: DealershipBankAccount | None,
    language: SwissLanguage,
) -> str:
    """The running footer on every page: the document's own footer text
    (KAN-208), then the master-data columns, read now (A-27: the legal
    entity, never a site)."""

    text_html = f'<div class="doc-footer-text">{_esc(footer_text)}</div>' if footer_text else ""
    resolved = letterhead.footer_columns(dealership, columns=columns, bank_account=bank_account, language=language)
    columns_html = "".join(
        f'<div class="doc-footer-column {column.value}">{"<br>".join(_esc(line) for line in lines)}</div>'
        for column, lines in resolved
    )
    columns_block = f'<div class="doc-footer-columns">{columns_html}</div>' if columns_html else ""
    return f'<div class="doc-footer">{text_html}{columns_block}</div>'


def _template_field(template: DocumentTemplate | None, prefix: str, language: SwissLanguage) -> str | None:
    if template is None:
        return None
    return getattr(template, f"{prefix}_{language.value}")


def _compose_html(
    *,
    dealership: Dealership,
    template: DocumentTemplate | None,
    location: Location | None,
    correspondence_language: SwissLanguage,
    content: ContentDefinition,
    logo: StoredObject | None = None,
    bank_account: DealershipBankAccount | None = None,
    signatures: Mapping[uuid.UUID, StoredObject] | None = None,
    watermark: DocumentWatermark | None = None,
) -> str:
    layout = Layout.of(template)
    header_note = _template_field(template, "header_note", correspondence_language)
    # The footer wording is the document's own standard text (KAN-208),
    # frozen in its content — no longer a template column.
    footer_html = _render_footer(
        dealership,
        footer_text=content.footer_text,
        columns=layout.footer_columns,
        bank_account=bank_account,
        language=correspondence_language,
    )
    body_blocks = "".join(_render_block(block, signatures or {}) for block in content.blocks)
    header_note_html = f'<div class="doc-paragraph">{_esc(header_note)}</div>' if header_note else ""

    watermark_word = None
    if watermark is DocumentWatermark.SAMPLE:
        watermark_word = letterhead.word(correspondence_language, "sample")
    elif watermark is DocumentWatermark.DRAFT and layout.draft_watermark:
        watermark_word = letterhead.word(correspondence_language, "draft")
    watermark_html = f'<div class="doc-watermark">{_esc(watermark_word)}</div>' if watermark_word else ""

    logo_html = _render_logo(dealership, logo, layout.logo_size)
    sender_html = f'<div class="doc-dealer">{_letterhead_lines(dealership, location)}</div>'
    # Source order is visual order (WeasyPrint's row-reverse clips the logo).
    header_html = sender_html + logo_html if layout.header_layout is HeaderLayout.LOGO_RIGHT else logo_html + sender_html

    style = _page_style(page_numbers=layout.page_numbers, language=correspondence_language) + _STYLE
    return f"""<!doctype html>
<html lang="{correspondence_language.value}">
<head><meta charset="utf-8"><style>{style}</style></head>
<body>
  {footer_html}
  {watermark_html}
  <div class="doc-head {layout.header_layout.value}">{header_html}</div>
  {_render_window(content.addressee, content.metadata or [], layout.address_window)}
  {header_note_html}
  <h1 class="doc-title">{_esc(content.title)}</h1>
  {body_blocks}
</body>
</html>"""


def _bank_account(db: Session, dealership_id: uuid.UUID, location: Location | None) -> DealershipBankAccount | None:
    """The banking column's account: the site's own account when the
    document is printed for a location that has one (the ADR-051 addendum),
    else the Betrieb's default — never a second Betrieb's."""

    if location is not None and location.bank_account_override_id is not None:
        own = db.scalar(
            select(DealershipBankAccount).where(
                DealershipBankAccount.id == location.bank_account_override_id,
                DealershipBankAccount.dealership_id == dealership_id,
            )
        )
        if own is not None:
            return own
    return db.scalar(
        select(DealershipBankAccount).where(
            DealershipBankAccount.dealership_id == dealership_id,
            DealershipBankAccount.is_default.is_(True),
        )
    )


def _signature_images(
    db: Session, *, dealership_id: uuid.UUID, content: ContentDefinition, store: ObjectStore | None
) -> dict[uuid.UUID, StoredObject]:
    """The signature images the content's signature blocks name, read now
    (Q2 A: a reprint shows the current image). Only a user who acts in the
    rendering Betrieb — homed there or an active member — prints; a user
    without an image, or one whose file is gone, prints the bare line."""

    signer_ids = {
        signer
        for block in content.blocks
        if isinstance(block, SignatureBlock)
        for signer in block.signer_user_ids
        if signer is not None
    }
    images: dict[uuid.UUID, StoredObject] = {}
    for signer_id in sorted(signer_ids, key=str):
        user = get_user_acting_in(db, dealership_id=dealership_id, user_id=signer_id)
        if user is None or user.signature_image_ref is None:
            continue
        stored = (store or get_object_store()).get(user.signature_image_ref)
        if stored is not None:
            images[signer_id] = stored
    return images


def compose_document_html(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    correspondence_language: SwissLanguage,
    content: ContentDefinition,
    location_id: uuid.UUID | None = None,
    store: ObjectStore | None = None,
    watermark: DocumentWatermark | None = None,
) -> str:
    """Everything `render_document` prints, as the HTML handed to WeasyPrint."""

    dealership = get_dealership_or_404(db, dealership_id)
    template = get_document_template(db, dealership_id)
    # KAN-666: the location is resolved inside the rendering Betrieb — another
    # Betrieb's site is a 404 (rule 7), never a letterhead.
    location = (
        get_location_or_404(db, dealership_id=dealership_id, location_id=location_id)
        if location_id is not None
        else None
    )
    # KAN-205/KAN-206: the header's logo comes from the object store — the
    # site's own when it has one — `store` is the caller's (a route's
    # dependency), else the application's.
    logo = document_header_logo(db, dealership, store=store, location_id=location.id if location else None)
    return _compose_html(
        dealership=dealership,
        template=template,
        location=location,
        correspondence_language=correspondence_language,
        content=content,
        logo=logo,
        bank_account=_bank_account(db, dealership_id, location),
        signatures=_signature_images(db, dealership_id=dealership_id, content=content, store=store),
        watermark=watermark,
    )


def render_document(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    correspondence_language: SwissLanguage,
    content: ContentDefinition,
    location_id: uuid.UUID | None = None,
    store: ObjectStore | None = None,
    watermark: DocumentWatermark | None = None,
) -> bytes:
    """Renders `content` inside `dealership`'s shared frame (letterhead,
    the addressee in the envelope window, the master-data footer, the
    dealership's own header note if any, the content's own footer standard
    text) as a PDF, in `correspondence_language` — never the caller's UI
    locale. `location_id` prints the document for that site (its address
    block and logo, its own bank account; the legal footer is unchanged,
    A-27); `watermark` prints DRAFT (while the template has it on) or SAMPLE
    across every page. Tolerates a dealership with no DocumentTemplate row
    at all (the shipped layout, empty boilerplate, never an error) so a
    brand-new dealership can generate documents on day one.
    """

    return write_pdf(
        compose_document_html(
            db,
            dealership_id=dealership_id,
            correspondence_language=correspondence_language,
            content=content,
            location_id=location_id,
            store=store,
            watermark=watermark,
        )
    )


def local_url_fetcher() -> URLFetcher:
    """What WeasyPrint may load while it renders: `data:` URIs and nothing
    else (KAN-205 review). Every byte a document prints is in its own HTML —
    the logo inlined from the object store, an SVG logo's inline rasters —
    so a URL to anywhere else (http, https, file, ftp, a relative path) is
    refused before any connection is made. It is the second wall behind the
    SVG sanitiser: a stylesheet or image reference that slipped through it
    still reaches nothing. A fresh fetcher per render — it keeps request
    state between calls."""

    return URLFetcher(allowed_protocols={"data"}, allow_redirects=False, timeout=1)


def layout_document(html_string: str) -> Document:
    """WeasyPrint's laid-out document — the pages and boxes a PDF is drawn
    from — for the geometry checks (the envelope window is measured on it)."""

    return HTML(string=html_string, url_fetcher=local_url_fetcher()).render()


def write_pdf(html_string: str) -> bytes:
    return HTML(string=html_string, url_fetcher=local_url_fetcher()).write_pdf()
