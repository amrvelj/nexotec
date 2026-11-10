"""KAN-216: the catalogue declaration is complete in four languages (CLAUDE.md
i18n; D-A-40, FR-A-47/48).

Every event's title and body key is in the SPA's four bundles, and every
push title the backend renders is the bundle's title word for word — the
inbox (rendered by the SPA) and the push (rendered by the worker) never say
two different things. The security family is locked, as FR-A-48 rules.
"""

from pathlib import Path

from app.platform.models.notification import NotificationFamily, NotificationTiming
from app.platform.services import notification_catalogue
from app.platform.services.notification_catalogue import CATALOGUE, DIGEST_PUSH_TITLE, DIGEST_PUSH_TITLE_KEY, LANGUAGES
from app.platform.services.notifications import LOCKED_FAMILIES
from scripts import import_ui_translation_bundles

_LOCALES = Path(__file__).resolve().parents[1] / "frontend" / "apps" / "dms" / "src" / "i18n" / "locales"


def _bundle(language: str) -> dict:
    # KAN-616: locales/<language>/<namespace>.json, assembled as the SPA's locales/<language>.ts does
    return import_ui_translation_bundles.read_bundle(_LOCALES / language)


def _lookup(bundle: dict, key: str):
    node = bundle
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def test_every_event_has_its_title_and_body_in_every_bundle():
    missing = [
        f"{language}: {key}"
        for language in LANGUAGES
        for spec in CATALOGUE
        for key in (spec.title_key, spec.body_key)
        if not isinstance(_lookup(_bundle(language), key), str) or not _lookup(_bundle(language), key).strip()
    ]
    assert not missing, "\n".join(missing)


def test_every_push_title_is_the_bundles_title():
    differ = [
        f"{language}: {spec.code.value}: push {spec.push_title[language]!r} != bundle {_lookup(_bundle(language), spec.title_key)!r}"
        for language in LANGUAGES
        for spec in CATALOGUE
        if spec.push_title.get(language) != _lookup(_bundle(language), spec.title_key)
    ]
    differ += [
        f"{language}: digest {DIGEST_PUSH_TITLE[language]!r}"
        for language in LANGUAGES
        if DIGEST_PUSH_TITLE[language] != _lookup(_bundle(language), DIGEST_PUSH_TITLE_KEY)
    ]
    assert not differ, "\n".join(differ)


def test_the_security_family_is_locked_and_immediate():
    assert NotificationFamily.SECURITY in LOCKED_FAMILIES
    for spec in CATALOGUE:
        if spec.family in LOCKED_FAMILIES:
            assert spec.switchable is False and spec.default_timing == NotificationTiming.IMMEDIATE, spec.code


def test_codes_are_unique_and_named_by_their_family():
    codes = [spec.code.value for spec in CATALOGUE]
    assert len(codes) == len(set(codes)) == len(notification_catalogue.NotificationEventCode)
    for spec in CATALOGUE:
        assert spec.code.value.startswith(spec.family.value + "."), spec.code


def test_an_unknown_language_falls_back_to_german():
    assert notification_catalogue.push_title("security.account_locked", None) == "Konto vorübergehend gesperrt"
    assert notification_catalogue.push_title("security.account_locked", "es") == "Konto vorübergehend gesperrt"
    assert notification_catalogue.push_title("security.account_locked", "it") == "Account temporaneamente bloccato"
