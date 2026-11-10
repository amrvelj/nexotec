"""KAN-616: the UI-string bundles live as `locales/<language>/<namespace>.json`.

The split script moves a one-file-per-language bundle into namespace files (a namespace above the
size cap split by its second level) and writes the typed `locales/<language>.ts` that assembles
them; the comparison script reads either layout. These tests run both on a throwaway folder; the
real bundles' parity, cap and index are `localeKeyParity.test.ts`'s.
"""

import json

import pytest

from scripts import compare_locale_bundles, import_ui_translation_bundles, split_locale_bundles

BUNDLE = {
    "common": {"retry": "Erneut", "nested": {"a": "A"}},
    "apiRefusal": {"notFound": "Nicht gefunden", "reasons": {"vin": "FIN", "km": "Kilometer"}, "forms": {"x": "y"}},
}


@pytest.fixture
def locales(tmp_path, monkeypatch):
    for language in split_locale_bundles.LANGUAGES:
        (tmp_path / f"{language}.json").write_text(split_locale_bundles.dump(BUNDLE), encoding="utf-8")
    # Small enough that apiRefusal (and only it) is split by its second level.
    size = len(split_locale_bundles.dump(BUNDLE["apiRefusal"]).encode())
    monkeypatch.setattr(split_locale_bundles, "MAX_FILE_BYTES", size - 1)
    return tmp_path


def test_split_writes_one_file_per_namespace_and_splits_an_oversized_one(locales):
    split_locale_bundles.main(["split", "--locales-dir", str(locales)])

    assert not list(locales.glob("*.json"))
    assert sorted(p.name for p in (locales / "de").iterdir()) == [
        "apiRefusal.forms.json", "apiRefusal.json", "apiRefusal.reasons.json", "common.json",
    ]
    assert json.loads((locales / "de" / "apiRefusal.json").read_text()) == {"notFound": "Nicht gefunden"}
    index = (locales / "de.ts").read_text()
    assert "import ns_apiRefusal__reasons from './de/apiRefusal.reasons.json'" in index
    assert "apiRefusal: { ...ns_apiRefusal, forms: ns_apiRefusal__forms, reasons: ns_apiRefusal__reasons }," in index
    assert "  common: ns_common," in index


def test_every_reader_assembles_the_split_files_back_into_the_same_bundle(locales):
    split_locale_bundles.main(["split", "--locales-dir", str(locales)])

    names = [str(p.relative_to(locales)) for p in locales.rglob("*.json")]
    assembled = compare_locale_bundles.assemble("fr", names, lambda n: (locales / n).read_text(encoding="utf-8"))
    assert assembled == BUNDLE
    assert import_ui_translation_bundles.read_bundle(locales / "fr") == BUNDLE


def test_split_leaves_a_language_that_already_has_its_folder_alone(locales, capsys):
    split_locale_bundles.main(["split", "--locales-dir", str(locales)])
    split_locale_bundles.main(["split", "--locales-dir", str(locales)])

    assert "de/ exists - left alone" in capsys.readouterr().out


def test_the_real_bundles_read_the_same_through_the_import_script_and_the_comparison(tmp_path):
    """The import script and the comparison each carry a reader; on the shipped files they agree."""

    root = split_locale_bundles.LOCALES_DIR
    names = [str(p.relative_to(root)) for p in root.rglob("*.json")]
    for language in split_locale_bundles.LANGUAGES:
        via_compare = compare_locale_bundles.assemble(language, names, lambda n: (root / n).read_text(encoding="utf-8"))
        assert import_ui_translation_bundles.read_bundle(root / language) == via_compare
        assert len(compare_locale_bundles.flatten(via_compare)) > 1000
