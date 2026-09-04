"""KAN-23: app.sales.i18n's own document-string vocabulary — a missing key
in one language is a build-time bug (CLAUDE.md's own rule: no German
fallback, a loud marker instead — here, t() raising rather than a
document going out with a placeholder in it).
"""

import pytest

from app.core.i18n import SwissLanguage
from app.sales.i18n import _STRINGS, t


def test_every_language_defines_exactly_the_same_keys():
    key_sets = {language: set(strings) for language, strings in _STRINGS.items()}
    reference = key_sets[SwissLanguage.DE]
    for language, keys in key_sets.items():
        assert keys == reference, f"{language.value} key set diverges from DE: {keys.symmetric_difference(reference)}"


def test_all_four_languages_are_defined():
    assert set(_STRINGS) == {SwissLanguage.DE, SwissLanguage.FR, SwissLanguage.IT, SwissLanguage.EN}


def test_t_formats_placeholders():
    assert t(SwissLanguage.DE, "offer.title", number="O-000123") == "Offerte O-000123"
    assert t(SwissLanguage.FR, "offer.title", number="O-000123") == "Offre O-000123"
    assert t(SwissLanguage.EN, "priceBuildUp.includedVat", rate="8.1") == "VAT included (8.1%)"


def test_t_raises_never_falls_back_to_german():
    with pytest.raises(KeyError):
        t(SwissLanguage.FR, "this.key.does.not.exist")


def test_every_key_used_by_document_py_is_defined():
    """The reverse direction — a key document.py references that this
    module doesn't define would only surface as a runtime KeyError deep
    inside a document generation call. Cheaper to catch here.
    """

    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "sales" / "services" / "document.py").read_text()
    used_keys = set(re.findall(r't\(language,\s*"([^"]+)"', source))
    assert used_keys, "no t(language, \"...\") calls found — did document.py stop using app.sales.i18n?"
    defined_keys = set(_STRINGS[SwissLanguage.DE])
    missing = used_keys - defined_keys
    assert not missing, f"document.py references undefined i18n key(s): {missing}"
