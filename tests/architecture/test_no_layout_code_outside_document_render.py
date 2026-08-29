"""WP-6b PR-3: "add a check" — the brief's own literal instruction against
a second, throwaway document renderer ever growing somewhere else (exactly
what happened before ADR-051, when this was left to WP-9 alone and WP-7/
WP-8 each would have built their own). Two independent guards, introspecting
the actual source tree rather than trusting a code review to catch either:

1. `weasyprint` is imported in exactly one file under app/.
2. No raw HTML-tag string literal (<table, <div, <style, <html) appears
   anywhere under app/ outside that one file — a future module building its
   own "just a quick inline HTML string" content block fails CI immediately.
"""

import re
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent.parent / "app"
_ALLOWED_FILE = _APP_ROOT / "platform" / "services" / "document_render.py"

_WEASYPRINT_IMPORT = re.compile(r"^\s*(import weasyprint\b|from weasyprint\b)", re.MULTILINE)
_HTML_TAG_LITERAL = re.compile(r"<(table|div|style|html)\b", re.IGNORECASE)


def _all_app_py_files() -> list[Path]:
    return sorted(_APP_ROOT.rglob("*.py"))


def test_weasyprint_is_imported_in_exactly_one_file():
    importers = [f for f in _all_app_py_files() if _WEASYPRINT_IMPORT.search(f.read_text(encoding="utf-8"))]
    assert importers == [_ALLOWED_FILE], (
        f"expected only {_ALLOWED_FILE} to import weasyprint, found: {importers}"
    )


def test_no_html_tag_literals_outside_document_render():
    offenders = {
        f: _HTML_TAG_LITERAL.findall(f.read_text(encoding="utf-8"))
        for f in _all_app_py_files()
        if f != _ALLOWED_FILE
    }
    offenders = {f: tags for f, tags in offenders.items() if tags}
    assert not offenders, (
        "found HTML-tag-shaped string literals outside "
        f"{_ALLOWED_FILE} — layout code belongs only there: {offenders}"
    )
