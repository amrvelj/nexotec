"""Every token of the standard texts' closed set (`Placeholder`) has its
«Platzhalter einfügen» label in all four bundles, under the key the SPA builds
from it (`standardTextModel.ts::placeholderKey`: the token with its dot as an
underscore). KAN-464 PR-3: `customer.salutation` kept its name when customers
became contacts (Anto, 2026-11-24), so its label key keeps it too — a renamed
key rendered the raw key on the button. The tokens come from the enum, so a
new one without a label fails here."""

import json
from pathlib import Path

import pytest

from app.platform.documents.services.standard_text_placeholders import Placeholder

_LOCALES = Path(__file__).resolve().parents[2] / "frontend" / "apps" / "dms" / "src" / "i18n" / "locales"
_MODEL = Path(__file__).resolve().parents[2] / "frontend" / "apps" / "dms" / "src" / "components" / "standardTexts" / "standardTextModel.ts"


def test_the_spa_builds_the_label_key_this_test_reads():
    assert "`standardTexts.placeholder.${token.replace('.', '_')}`" in _MODEL.read_text(encoding="utf-8")


@pytest.mark.parametrize("language", ["de", "fr", "it", "en"])
def test_every_placeholder_has_a_label(language):
    labels = json.loads((_LOCALES / language / "standardTexts.json").read_text(encoding="utf-8"))["placeholder"]
    missing = [token.value for token in Placeholder if not isinstance(labels.get(token.value.replace(".", "_")), str)]
    assert missing == []
