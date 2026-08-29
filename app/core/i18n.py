"""The shared four-language vocabulary (WP-6b PR-1) and Swiss formatting
helpers — cross-cutting, owned by no bounded context, importable by every
other module (app.core imports no bounded context, per the import-linter
contract, and is designed to be reused everywhere a context needs "one of
the four shipped languages" or "format this the Swiss way").

`SwissLanguage` is deliberately NOT the same Python type as
`app.customer.models.customer.Language` — the two enums are structurally
identical (DE/FR/IT/EN) but app.core may never import a bounded context, so
this is a second, independent definition kept in sync by convention, the
same way this codebase already keeps enum/reference-list pairs in sync
elsewhere (e.g. PR-1 of WP-5's vehicle_kind vs the shipped table's
vehicle_type). A context that already has its own language-shaped field
(Customer.language) keeps using its own type; this one is for everyone
else — chiefly app.platform's document-template layer, whose
`correspondence_language` parameter is this type, never a bare string.

Date and number formatting are UI/UX Specification "Internationalisation
and Formatting" table — one example row each, not four — so they are
identical across all four languages and take no language parameter at
all. Only which language a document's own content strings are written in
varies; that's the caller's concern, not this module's.
"""

import decimal
import enum
from datetime import date


class SwissLanguage(str, enum.Enum):
    """DE/FR/IT/EN — the four languages the application ships translations
    for. See this module's own docstring for why this is a second
    definition of the same four values as app.customer's Language, not a
    shared import.
    """

    DE = "de"
    FR = "fr"
    IT = "it"
    EN = "en"


def format_date_ch(value: date) -> str:
    """`dd.MM.yyyy`, e.g. `08.08.2026` — identical in all four languages
    (UI/UX Specification § Internationalisation and Formatting).
    """

    return value.strftime("%d.%m.%Y")


def format_number_ch(value: decimal.Decimal | int) -> str:
    """Apostrophe thousands separator, e.g. `12'482`.

    The straight ASCII apostrophe `U+0027`, never the typographic `U+2019`
    that `Intl.NumberFormat('de-CH')` emits by default in the browser — the
    UI/UX spec rules (2026-08-16) that the ASCII form is mandatory in the
    UI and on every printed document, so it is normalized here explicitly
    rather than assumed. Deliberately not implemented via Python's
    `locale` module: this app's own Postgres container logs ("no usable
    system locales were found") already show the `slim` base images it
    deploys on can't be trusted to have Swiss locale data installed, so
    correctness here does not depend on OS locale configuration at all.
    """

    return f"{value:,}".replace(",", "'")


def format_currency_chf(value: decimal.Decimal) -> str:
    """`CHF 12'500.00` — always two decimals, right-aligned/mono is a
    layout concern for the caller (a table cell), not this formatter's.
    """

    quantized = value.quantize(decimal.Decimal("0.01"))
    sign = "− " if quantized < 0 else ""  # real minus sign U+2212, not a hyphen
    whole, _, frac = str(abs(quantized)).partition(".")
    return f"{sign}CHF {format_number_ch(int(whole))}.{frac:0<2}"
