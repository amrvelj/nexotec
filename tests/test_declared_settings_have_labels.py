"""Every setting an area declares in `app/setting_registry.py` has its label
in the SPA's four `settings` bundles (`settings.keys.<area>.<name>.label`) —
the setup checklist names a setting item by it (KAN-805 PR-3), and the
settings screen will (PR-5). A setting without one would show «MISSING I18N
KEY» to the manager; this fails first, as test_status_sources_have_labels
does for the status page. A choice's codes need their labels too
(`…<name>.options.<code>`)."""

import json
import re
from pathlib import Path

import pytest

import app.setting_registry  # noqa: F401 — registers every area's declarations
from app.platform.organisation.services.setting_declarations import SettingKind, declared_settings

_DMS = Path(__file__).resolve().parents[1] / "frontend" / "apps" / "dms" / "src"
_LOCALES = _DMS / "i18n" / "locales"


def missing_labels(bundle: dict, declarations) -> list[str]:
    keys = bundle.get("keys", {})
    missing = []
    for declaration in declarations:
        area, name = declaration.key.split(".", 1)
        entry = keys.get(area, {}).get(name, {})
        if not entry.get("label"):
            missing.append(f"{declaration.key}.label")
        if SettingKind(declaration.kind) == SettingKind.CHOICE:
            options = entry.get("options", {})
            missing += [f"{declaration.key}.options.{code}" for code in declaration.choices if not options.get(code)]
    return missing


@pytest.mark.parametrize("language", ["de", "en", "fr", "it"])
def test_every_declared_setting_has_its_label(language):
    bundle = json.loads((_LOCALES / language / "settings.json").read_text(encoding="utf-8"))
    shipped = [d for d in declared_settings() if not d.key.startswith("testarea.")]
    assert missing_labels(bundle, shipped) == [], f"settings.keys lacks labels in {language}"


def test_the_rule_on_a_sample():
    from tests import setting_store_support as samples

    bundle = {"keys": {"testarea": {"time_unit": {"label": "Zeiteinheit", "options": {"hours": "Stunden"}}}}}
    assert missing_labels(bundle, [samples.UNIT, samples.WAIT]) == [
        "testarea.time_unit.options.work_units",
        "testarea.wait_minutes.label",
    ]


def test_a_checklist_setting_links_to_a_screen_the_spa_serves():
    """A setting item links to `/administration/settings/<area>` (KAN-805 PR-3);
    the screen comes with PR-5. Until the SPA serves the route, no shipped
    declaration may be a checklist item — its link would lead nowhere."""

    items = [d for d in declared_settings() if d.checklist_item and not d.key.startswith("testarea.")]
    routes = set(re.findall(r'path="([^"]+)"', (_DMS / "App.tsx").read_text(encoding="utf-8")))
    served = {"/administration/settings/:area"} & routes
    assert not items or served, f"{[d.key for d in items]} link to /administration/settings/<area>, which App.tsx does not serve"
