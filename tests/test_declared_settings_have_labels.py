"""Every setting an area declares in `app/setting_registry.py` has its label
in the SPA's four `settings` bundles (`settings.keys.<area>.<name>.label`) —
the setup checklist names a setting item by it (KAN-805 PR-3), and the
settings screen does (PR-5), which also titles each area (`areas.<area>`) and
words a number's unit (`units.<unit>`). A setting without one would show «MISSING I18N
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
    # The settings screen (PR-5) titles an area and words a number's unit.
    for area in sorted({declaration.area for declaration in declarations}):
        if not bundle.get("areas", {}).get(area):
            missing.append(f"areas.{area}")
    for unit in sorted({declaration.unit for declaration in declarations if declaration.unit}):
        if not bundle.get("units", {}).get(unit):
            missing.append(f"units.{unit}")
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
        "areas.testarea",
        "units.minutes",
        "testarea.time_unit.options.work_units",
        "testarea.wait_minutes.label",
    ]
    complete = {**bundle, "areas": {"testarea": "Test"}, "units": {"minutes": "Minuten"}}
    assert missing_labels(complete, [samples.UNIT]) == ["testarea.time_unit.options.work_units"]


def test_a_checklist_setting_links_to_a_screen_the_spa_serves():
    """A setting item links to `/administration/settings/<area>` (KAN-805 PR-3),
    which the generic settings screen serves (PR-5)."""

    items = [d for d in declared_settings() if d.checklist_item and not d.key.startswith("testarea.")]
    routes = set(re.findall(r'path="([^"]+)"', (_DMS / "App.tsx").read_text(encoding="utf-8")))
    assert not items or "/administration/settings/:area" in routes, (
        f"{[d.key for d in items]} link to /administration/settings/<area>, which App.tsx does not serve"
    )


def test_no_static_route_hides_an_area_from_the_generic_settings_screen():
    """KAN-805 criterion 6: the generic settings screen shows every area; a topic
    screen (KAN-806's «Stundenansätze») embeds the same rows under its own path. A
    static /administration/settings/<area> route would outrank `:area` and hide
    that area from the generic screen (ADR-219 addendum)."""

    routes = set(re.findall(r'path="([^"]+)"', (_DMS / "App.tsx").read_text(encoding="utf-8")))
    shadowing = sorted(r for r in routes if r.startswith("/administration/settings/") and ":" not in r)
    assert shadowing == [], f"{shadowing} hide their area from the generic settings screen: remove them"
