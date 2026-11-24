"""Every status-page source registered in `app/status_registry.py` has its
section label in the SPA's four statusPage bundles — a source without one
renders «MISSING I18N KEY» as its heading (found on KAN-784's screenshot)."""

import json
from pathlib import Path

import pytest

from app.core import status
from app.status_registry import register_status_providers

_LOCALES = Path(__file__).resolve().parents[1] / "frontend" / "apps" / "dms" / "src" / "i18n" / "locales"
# Derived by platform itself, not registered (status_page.py).
_PLATFORM_SOURCES = {"number_ranges", "nexotec"}


def _registered_sources() -> set[str]:
    register_status_providers()
    return {provider.source for provider in status.status_providers()}


@pytest.mark.parametrize("language", ["de", "en", "fr", "it"])
def test_every_status_source_has_a_label(language):
    labels = json.loads((_LOCALES / language / "statusPage.json").read_text(encoding="utf-8"))["sources"]
    missing = sorted((_registered_sources() | _PLATFORM_SOURCES) - set(labels))
    assert missing == [], f"statusPage.sources lacks {missing} in {language}"
