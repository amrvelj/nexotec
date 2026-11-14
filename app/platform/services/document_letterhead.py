"""What the letterhead prints and where (KAN-206; Dealer Administration PRD,
"Letterhead and the document template"; Anto's answers of 2026-11-12).

This module decides *content and geometry* and holds no markup:
`document_render.py` stays the one place that knows what a document looks
like (ADR-051, `test_no_layout_code_outside_document_render.py`). Here:

- **The envelope window** (Q1 B): left or right per Betrieb, default right,
  with a fold mark. The millimetre values are Swiss Post's (see
  `WINDOW_GEOMETRY`); the renderer places the addressee there absolutely and
  `tests/test_document_letterhead.py` measures WeasyPrint's laid-out box
  against them.
- **The logo size** S/M/L — 30/40/50 mm wide at most, 20 mm high at most.
- **The footer's four columns**, resolved from master data at render time
  (never copied into the template): company and address · contact · banking
  · register, UID and VAT. The company column is the legal entity's
  registered seat, whatever site the document is printed for (A-27); a
  site's own bank account prints in the banking column when it has one (the
  ADR-051 addendum), else the Betrieb's default account. A column with no
  data is left out.
- **The fixed words a document prints** in its correspondence language —
  page numbers, the draft and sample watermarks, the footer's labels — in
  DE/FR/IT/EN, never the reader's UI language.
"""

import dataclasses
import re
from collections.abc import Sequence

from app.core.i18n import SwissLanguage
from app.platform.models.document_template import AddressWindow, FooterColumn, LogoSize
from app.platform.organisation.models.dealership import Dealership
from app.platform.organisation.models.dealership_bank_account import DealershipBankAccount

# --- the page -----------------------------------------------------------------

PAGE_WIDTH_MM = 210.0
PAGE_HEIGHT_MM = 297.0
# The page's own margins. The window, the fold mark and the header band are
# placed absolutely in page coordinates and converted against these.
MARGIN_TOP_MM = 15.0
MARGIN_RIGHT_MM = 20.0
MARGIN_BOTTOM_MM = 32.0
MARGIN_LEFT_MM = 20.0
# The letterhead band on page 1: from the top margin down to just above the
# window, so a tall logo can never push the addressee out of it.
HEADER_HEIGHT_MM = 28.0


@dataclasses.dataclass(frozen=True)
class WindowGeometry:
    """The part of an A4 sheet that shows through the envelope window,
    measured from the sheet's top-left corner, in millimetres."""

    left: float
    top: float
    width: float
    height: float


# Swiss Post's published positions for its standard window envelope
# (C5, window 50 x 100 mm, an A4 sheet folded once; a C4 envelope shows the
# unfolded sheet at the same place): ePost «Standard Fenster & PP – Position»
# (SmartSend – Formatierungsvorgaben, epost.ch, read 2026-11-14), measured from
# the sheet's upper left edge — the area «welche im Kuvertfenster zu jederzeit
# sichtbar ist»:
#   window right: 117 mm to 196 mm from the left edge, 47 mm to 81 mm from the top;
#   window left:   15 mm to  95 mm from the left edge, 47 mm to 81 mm from the top.
# The addressee prints inside it, so a shift of the sheet in the envelope never
# hides a line. To be confirmed with a test print (Anto's ruling, Q1 B).
WINDOW_GEOMETRY: dict[AddressWindow, WindowGeometry] = {
    AddressWindow.RIGHT: WindowGeometry(left=117.0, top=47.0, width=79.0, height=34.0),
    AddressWindow.LEFT: WindowGeometry(left=15.0, top=47.0, width=80.0, height=34.0),
}
# Where the body starts on page 1: below the window, clear of it.
BODY_TOP_MM = 92.0

# The fold mark: a C5 envelope takes the A4 sheet folded once, at half its
# height; a C4 envelope takes it unfolded. Printed on page 1, in the left
# margin.
FOLD_MARK_TOP_MM = PAGE_HEIGHT_MM / 2
FOLD_MARK_LEFT_MM = 5.0
FOLD_MARK_LENGTH_MM = 5.0

# --- the logo -------------------------------------------------------------------

LOGO_MAX_WIDTH_MM: dict[LogoSize, float] = {LogoSize.S: 30.0, LogoSize.M: 40.0, LogoSize.L: 50.0}
LOGO_MAX_HEIGHT_MM = 20.0

# --- the fixed words ------------------------------------------------------------

_WORDS: dict[SwissLanguage, dict[str, str]] = {
    SwissLanguage.DE: {
        "page": "Seite {page} von {pages}",
        "draft": "ENTWURF",
        "sample": "MUSTER",
        "phone": "Tel.",
        "register": "Handelsregister",
        "uid": "UID",
    },
    SwissLanguage.FR: {
        "page": "Page {page} sur {pages}",
        "draft": "PROJET",
        "sample": "SPÉCIMEN",
        "phone": "Tél.",
        "register": "Registre du commerce",
        "uid": "IDE",
    },
    SwissLanguage.IT: {
        "page": "Pagina {page} di {pages}",
        "draft": "BOZZA",
        "sample": "FACSIMILE",
        "phone": "Tel.",
        "register": "Registro di commercio",
        "uid": "IDI",
    },
    SwissLanguage.EN: {
        "page": "Page {page} of {pages}",
        "draft": "DRAFT",
        "sample": "SAMPLE",
        "phone": "Phone",
        "register": "Commercial register",
        "uid": "UID",
    },
}


def word(language: SwissLanguage, key: str) -> str:
    return _WORDS[language][key]


def page_number_parts(language: SwissLanguage) -> tuple[str, str, str]:
    """«Seite », « von », "" — the text around the two page counters, which
    only the renderer's paged-media counters can fill."""

    template = word(language, "page")
    head, rest = template.split("{page}", 1)
    middle, tail = rest.split("{pages}", 1)
    return head, middle, tail


# --- formatting -----------------------------------------------------------------

_SWISS_E164 = re.compile(r"^\+41(\d{2})(\d{3})(\d{2})(\d{2})$")


def format_phone(value: str) -> str:
    """A Swiss number as it prints: +41 44 123 45 67; any other as stored."""

    match = _SWISS_E164.match(value)
    if match is None:
        return value
    return "+41 " + " ".join(match.groups())


def format_iban(value: str) -> str:
    """An IBAN in groups of four, as a payer reads it off paper."""

    compact = value.replace(" ", "")
    return " ".join(compact[i : i + 4] for i in range(0, len(compact), 4))


# --- the footer columns ----------------------------------------------------------


def _company_lines(dealership: Dealership) -> list[str]:
    # A-27: the legal entity at its registered seat — never a site's address.
    address = dealership.address
    return [
        dealership.legal_name,
        f"{address['street']} {address['house_number']}".strip(),
        f"{address['postal_code']} {address['locality']}".strip(),
    ]


def _contact_lines(dealership: Dealership, language: SwissLanguage) -> list[str]:
    lines = [f"{word(language, 'phone')} {format_phone(dealership.phone)}"] if dealership.phone else []
    return [*lines, *(value for value in (dealership.email, dealership.website) if value)]


def _banking_lines(account: DealershipBankAccount | None) -> list[str]:
    if account is None:
        return []
    lines = [account.bank_name, f"IBAN {format_iban(account.iban)}"]
    if account.bic:
        lines.append(f"BIC {account.bic}")
    return lines


def _register_lines(dealership: Dealership, language: SwissLanguage) -> list[str]:
    lines: list[str] = []
    if dealership.hr_number:
        office = f" {dealership.commercial_register_office}" if dealership.commercial_register_office else ""
        lines.append(f"{word(language, 'register')}{office}")
        lines.append(dealership.hr_number)
    # The VAT number is the UID with its MWST/TVA/IVA suffix: printed, it
    # says both. Without one (or not liable), the UID alone.
    if dealership.vat_liable and dealership.vat_number:
        lines.append(dealership.vat_number)
    elif dealership.uid:
        lines.append(f"{word(language, 'uid')} {dealership.uid}")
    return lines


def footer_columns(
    dealership: Dealership,
    *,
    columns: Sequence[str],
    bank_account: DealershipBankAccount | None,
    language: SwissLanguage,
) -> list[tuple[FooterColumn, list[str]]]:
    """The footer's columns in the template's order, each with the lines it
    prints, read from master data now. A column with nothing to print is
    left out; an unknown code (a row written by hand) is skipped."""

    resolved: list[tuple[FooterColumn, list[str]]] = []
    for code in columns:
        try:
            column = FooterColumn(code)
        except ValueError:
            continue
        if column is FooterColumn.COMPANY:
            lines = _company_lines(dealership)
        elif column is FooterColumn.CONTACT:
            lines = _contact_lines(dealership, language)
        elif column is FooterColumn.BANKING:
            lines = _banking_lines(bank_account)
        else:
            lines = _register_lines(dealership, language)
        lines = [line for line in lines if line]
        if lines:
            resolved.append((column, lines))
    return resolved
