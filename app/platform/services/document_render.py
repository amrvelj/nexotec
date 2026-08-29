"""The shared document-render layer (WP-6b PR-3, ADR-051): the one place in
this application allowed to know what a rendered document looks like.
`tests/architecture/test_no_layout_code_outside_document_render.py` is the
standing check on that — a query, not a code review.

Composing the HTML (`_compose_html`) is kept separate from the actual PDF
call (`render_document`) on purpose: most correctness questions ("is the
letterhead there", "did the footer pick up the right language", "does a
GRAND line double-rule") are about the HTML string, and answering them
doesn't need WeasyPrint installed or a real render to run — only the one
end-to-end test in this package's test suite calls `write_pdf()` for real.

Visual values (colours, spacing, radii, fonts) below are copied from
frontend/packages/ui-kit/src/tokens.ts, the canonical, currently-shipped
token source — NOT the interactive prototype's own CSS variables, which
were only the layout reference for how these tokens compose (see this
package's plan for the prototype's exact `.doc*` class values this
generalizes). Keep the two in sync by hand if either changes; there is no
shared build step between a Python string and a TypeScript module.
"""

import html
import re
import uuid

from sqlalchemy.orm import Session
from weasyprint import HTML

from app.core.i18n import SwissLanguage, format_currency_chf
from app.platform.models.dealership import Dealership, Location
from app.platform.models.document_template import DocumentTemplate
from app.platform.schemas.document_content import (
    Addressee,
    ContentDefinition,
    KeyValueBlock,
    KeyValueRow,
    LineItemsBlock,
    ParagraphBlock,
    SignatureBlock,
)
from app.platform.services.dealership import get_dealership_or_404, get_location_or_404
from app.platform.services.document_template import get_document_template

# --- print tokens, copied from frontend/packages/ui-kit/src/tokens.ts ------

_DEFAULT_BRAND_COLOR = "#7C3AED"  # purple[6] — same fallback Dealership's own column default uses
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_SLATE_2 = "#E2E8F0"
_SLATE_5 = "#64748B"
_SLATE_6 = "#475569"
_SLATE_9 = "#0F172A"
_RADIUS_MD = "8px"
_RADIUS_SM = "7px"
_FONT_FAMILY = 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'
_FONT_FAMILY_MONO = "ui-monospace, SF Mono, Menlo, Consolas, monospace"

_STYLE = f"""
@page {{
  size: A4;
  margin: 2.2cm 2cm 2.6cm 2cm;
  @bottom-center {{ content: element(doc-footer); }}
  @bottom-right {{ content: counter(page) " / " counter(pages); font-size: 9px; color: {_SLATE_5}; }}
}}
body {{ font-family: {_FONT_FAMILY}; font-size: 13px; color: {_SLATE_9}; margin: 0; }}
.doc-footer {{ position: running(doc-footer); font-size: 9px; color: {_SLATE_5}; }}
.doc-head {{ display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; }}
.doc-dealer {{ display: flex; gap: 10px; font-size: 12px; line-height: 1.5; color: {_SLATE_6}; }}
.doc-logo {{
  width: 32px; height: 32px; border-radius: {_RADIUS_MD}; color: #fff;
  display: flex; align-items: center; justify-content: center; font-weight: 800; flex-shrink: 0;
  overflow: hidden;
}}
.doc-logo img {{ width: 100%; height: 100%; object-fit: cover; }}
.doc-meta {{ font-size: 12px; text-align: right; white-space: nowrap; }}
.doc-meta-row {{ display: flex; gap: 10px; justify-content: flex-end; }}
.doc-meta-row span {{ color: {_SLATE_5}; }}
.doc-to {{ margin: 36px 0 28px; line-height: 1.6; }}
.doc-title {{ font-size: 19px; font-weight: 700; letter-spacing: -0.3px; margin: 0 0 20px; }}
.doc-kv {{ margin-bottom: 20px; }}
.doc-kv.boxed {{ border: 1px solid {_SLATE_2}; border-radius: {_RADIUS_MD}; padding: 12px 14px; }}
.doc-kv-heading {{ font-weight: 700; margin-bottom: 6px; }}
.doc-kv-row {{ display: flex; justify-content: space-between; gap: 8px; font-size: 12px; padding: 2px 0; }}
.doc-kv-row span {{ color: {_SLATE_5}; }}
.doc-kv-row b {{ font-family: {_FONT_FAMILY_MONO}; font-size: 12.5px; }}
.doc-paragraph {{ margin: 14px 0; line-height: 1.6; }}
.doc-lines {{ display: grid; margin: 14px 0; }}
.doc-line {{
  display: flex; justify-content: space-between; gap: 16px; padding: 6px 0;
  border-bottom: 1px solid #F1F5F9;
}}
.doc-line.sub {{ padding-left: 16px; color: {_SLATE_6}; }}
.doc-line.total {{ font-weight: 700; border-bottom: 1px solid #CBD5E1; }}
.doc-line.grand {{ font-size: 16px; border-bottom: 3px double {_SLATE_9}; }}
.doc-line .amount {{ font-family: {_FONT_FAMILY_MONO}; font-size: 12.5px; text-align: right; }}
.doc-sign {{ display: flex; gap: 40px; margin-top: 52px; }}
.doc-sign > div {{ flex: 1; }}
.doc-sign-line {{ border-top: 1px solid {_SLATE_9}; margin-bottom: 6px; }}
.doc-sign span {{ font-size: 11px; color: {_SLATE_5}; }}
"""


def _esc(value: str) -> str:
    # quote=False: every call site below places this in HTML TEXT content,
    # never inside an attribute value, so only &/</> need escaping —
    # quote=True would also turn the Swiss apostrophe thousands separator
    # (format_currency_chf) into `&#x27;`, which is wrong here.
    return html.escape(value, quote=False)


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


def _render_signature_block(block: SignatureBlock) -> str:
    columns = "".join(
        f'<div><div class="doc-sign-line"></div><span>{_esc(label)}</span></div>' for label in block.labels
    )
    return f'<div class="doc-sign">{columns}</div>'


def _render_block(block: KeyValueBlock | ParagraphBlock | LineItemsBlock | SignatureBlock) -> str:
    # isinstance dispatch rather than a dict-of-callables keyed by type —
    # each renderer takes a different concrete block type, which a dict of
    # mixed callables can't express cleanly for a type checker.
    if isinstance(block, KeyValueBlock):
        return _render_key_value_block(block)
    if isinstance(block, ParagraphBlock):
        return _render_paragraph_block(block)
    if isinstance(block, LineItemsBlock):
        return _render_line_items_block(block)
    return _render_signature_block(block)


def _render_addressee(addressee: Addressee | None) -> str:
    if addressee is None:
        return ""
    lines = "<br>".join(_esc(line) for line in addressee.lines)
    return f'<div class="doc-to">{lines}</div>'


def _render_metadata(metadata: list[KeyValueRow]) -> str:
    if not metadata:
        return ""
    rows = "".join(
        f'<div class="doc-meta-row"><span>{_esc(row.label)}</span><b>{_esc(row.value)}</b></div>'
        for row in metadata
    )
    return f'<div class="doc-meta">{rows}</div>'


def _render_logo(dealership: Dealership) -> str:
    """An uploaded logo (dealership.logo_url) if there is one; otherwise an
    initials mark in the dealership's own brand colour — the same fallback
    the frontend shell's own BrandMark component uses, so a dealership
    that hasn't uploaded a logo still renders an on-brand, recognizable
    letterhead rather than a generic placeholder.
    """

    if dealership.logo_url:
        return f'<img src="{_esc(dealership.logo_url)}" alt="">'
    color = dealership.brand_primary_color
    if not color or not _HEX_COLOR.match(color):
        color = _DEFAULT_BRAND_COLOR
    name = dealership.dba_name or dealership.legal_name
    initial = _esc(name[:1].upper()) if name else ""
    return f'<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:{color}">{initial}</div>'


def _letterhead_lines(dealership: Dealership) -> str:
    name = dealership.dba_name or dealership.legal_name
    address = dealership.address
    return (
        f"<b>{_esc(name)}</b><br>"
        f"{_esc(address['street'])} {_esc(address['house_number'])} · "
        f"{_esc(address['postal_code'])} {_esc(address['locality'])}<br>"
        f"{_esc(dealership.phone)}"
    )


def _footer_address_lines(dealership: Dealership, location: Location | None) -> str:
    if location is not None and location.address_street:
        name = location.name
        street = f"{location.address_street} {location.address_house_number or ''}".strip()
        locality = f"{location.address_postal_code or ''} {location.address_locality or ''}".strip()
        parts = [p for p in (name, street, locality) if p]
    else:
        address = dealership.address
        name = dealership.dba_name or dealership.legal_name
        street = f"{address['street']} {address['house_number']}"
        locality = f"{address['postal_code']} {address['locality']}"
        parts = [name, street, locality]
    return _esc(" · ".join(parts))


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
) -> str:
    header_note = _template_field(template, "header_note", correspondence_language)
    footer_text = _template_field(template, "footer_text", correspondence_language)

    body_blocks = "".join(_render_block(block) for block in content.blocks)

    header_note_html = f'<div class="doc-paragraph">{_esc(header_note)}</div>' if header_note else ""
    footer_boilerplate_html = f"<div>{_esc(footer_text)}</div>" if footer_text else ""

    return f"""<!doctype html>
<html lang="{correspondence_language.value}">
<head><meta charset="utf-8"><style>{_STYLE}</style></head>
<body>
  <div class="doc-footer">
    {footer_boilerplate_html}
    <div>{_footer_address_lines(dealership, location)}</div>
  </div>
  <div class="doc-head">
    <div class="doc-dealer">
      <div class="doc-logo">{_render_logo(dealership)}</div>
      <div>{_letterhead_lines(dealership)}</div>
    </div>
    {_render_metadata(content.metadata or [])}
  </div>
  {header_note_html}
  {_render_addressee(content.addressee)}
  <h1 class="doc-title">{_esc(content.title)}</h1>
  {body_blocks}
</body>
</html>"""


def render_document(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    correspondence_language: SwissLanguage,
    content: ContentDefinition,
    location_id: uuid.UUID | None = None,
) -> bytes:
    """Renders `content` inside `dealership`'s shared frame (letterhead,
    per-location footer address, the dealership's own template boilerplate
    if any) as a PDF, in `correspondence_language` — never the caller's UI
    locale. Tolerates a dealership with no DocumentTemplate row at all
    (renders with empty boilerplate, never an error) so a brand-new
    dealership can generate documents on day one.
    """

    dealership = get_dealership_or_404(db, dealership_id)
    template = get_document_template(db, dealership_id)
    location = get_location_or_404(db, location_id) if location_id is not None else None

    html_string = _compose_html(
        dealership=dealership,
        template=template,
        location=location,
        correspondence_language=correspondence_language,
        content=content,
    )
    return HTML(string=html_string).write_pdf()
