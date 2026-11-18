"""WP-6b PR-1: SwissLanguage + the Swiss date/number/currency formatters.

Model-level, no WeasyPrint or database needed — this is pure formatting
logic, tested directly against the UI/UX Specification's own worked
examples so a regression here is caught before it ever reaches a rendered
document.
"""

import datetime as dt
from decimal import Decimal

from app.core.i18n import SwissLanguage, format_currency_chf, format_date_ch, format_number_ch


def test_swiss_language_has_exactly_the_four_shipped_languages():
    assert {lang.value for lang in SwissLanguage} == {"de", "fr", "it", "en"}


def test_format_date_ch_matches_the_spec_example():
    assert format_date_ch(dt.date(2026, 8, 8)) == "08.08.2026"


def test_format_number_ch_uses_apostrophe_thousands_separator():
    assert format_number_ch(12482) == "12'482"


def test_format_number_ch_normalizes_to_the_ascii_apostrophe():
    # U+0027 required, never the typographic U+2019 that
    # Intl.NumberFormat('de-CH') emits by default in the browser (UI/UX
    # Specification, ruled 2026-08-16).
    result = format_number_ch(12482)
    assert "'" in result
    assert "’" not in result


def test_format_number_ch_handles_decimal_input():
    assert format_number_ch(Decimal(1234567)) == "1'234'567"


def test_format_currency_chf_matches_the_spec_examples():
    assert format_currency_chf(Decimal(12500)) == "CHF 12'500.00"
    assert format_currency_chf(Decimal(68900)) == "CHF 68'900.00"


def test_format_currency_chf_always_shows_two_decimals():
    assert format_currency_chf(Decimal(0)) == "CHF 0.00"
    assert format_currency_chf(Decimal("1234567.5")) == "CHF 1'234'567.50"


def test_format_currency_chf_negative_uses_the_real_minus_sign():
    # The prototype's own renderer (src/19-document.js) prefixes negative
    # line amounts with '− ' (a real minus sign), never an ASCII
    # hyphen — matched here for visual consistency with that reference.
    result = format_currency_chf(Decimal(-1200))
    assert result == "− CHF 1'200.00"
    assert "-CHF" not in result
