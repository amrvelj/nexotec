"""Turns the frontend's UI-string bundles into `bundle_seed` rows of
`ui_translation` (KAN-209, FR-A-30: "the bundles shipped by KAN-163 become the
initial seed").

A script, not a migration: the bundles live in the frontend tree
(`frontend/apps/dms/src/i18n/locales/{de,fr,it,en}/<namespace>.json`, KAN-616), which the backend
image does not carry (the Dockerfile copies only the built SPA), and they
keep changing in ordinary PRs — a migration reading them would write
different rows depending on the day it ran, and an immutable migration could
never be corrected. Run it where the repository is checked out (Render's
native Python service has it; in Docker pass `--bundles-dir` to a mounted
copy).

Dry run by default: the real import runs in one transaction that is rolled
back, and the report says what a `--commit` run would write. Idempotent: a
second run inserts nothing; a run after the bundles changed refreshes the
seed text of the keys they changed and adds the keys they gained. A
`platform` value is never touched — a row whose seed moved under a platform
edit is listed, so staff can review the edit against the new seed. It
publishes no event and bumps no revision: seed rows are never served
(`GET /v1/i18n/{language}` serves platform values; the bundle is the
fallback for everything else).

Usage:
    python scripts/import_ui_translation_bundles.py            # dry run
    python scripts/import_ui_translation_bundles.py --commit
    python scripts/import_ui_translation_bundles.py --bundles-dir /path/to/locales --commit
"""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.i18n import SwissLanguage
from app.db import SessionLocal
from app.platform.services.ui_translation import ImportReport, import_bundles

DEFAULT_BUNDLES_DIR = Path(__file__).resolve().parents[1] / "frontend" / "apps" / "dms" / "src" / "i18n" / "locales"


def read_bundle(folder: Path) -> dict:
    """One language's bundle from its namespace files (KAN-616): `<namespace>.json` is that top-level
    namespace, `<namespace>.<key>.json` one child of it split off for size — the same assembly as the
    generated `locales/<language>.ts` (scripts/split_locale_bundles.py)."""
    bundle: dict = {}
    for path in sorted(folder.glob("*.json")):
        namespace, _, key = path.stem.partition(".")
        content = json.loads(path.read_text(encoding="utf-8"))
        if key:
            bundle.setdefault(namespace, {})[key] = content
        elif isinstance(content, dict):
            bundle.setdefault(namespace, {}).update(content)
        else:
            bundle[namespace] = content
    return bundle


def read_bundles(directory: Path) -> dict[SwissLanguage, dict]:
    bundles: dict[SwissLanguage, dict] = {}
    for language in SwissLanguage:
        folder = directory / language.value
        if not folder.is_dir() or not any(folder.glob("*.json")):
            raise SystemExit(f"KAN-209 import: no bundle in {folder} — every language ships one; nothing was written.")
        bundles[language] = read_bundle(folder)
    return bundles


def run(db: Session, bundles: dict[SwissLanguage, dict], *, commit: bool) -> ImportReport:
    report = import_bundles(db, bundles)
    if commit:
        db.commit()
    else:
        db.rollback()
    return report


def _print_report(report: ImportReport, *, commit: bool) -> None:
    mode = "written" if commit else "DRY RUN — would write (rolled back; pass --commit)"
    print(f"ui_translation import: {mode}")
    print(f"  inserted bundle_seed rows: {report.inserted}")
    print(f"  seed text refreshed:       {report.seed_refreshed}")
    print(f"  unchanged:                 {report.unchanged}")
    if report.platform_rows_with_new_seed:
        print(f"  platform edits whose seed changed ({len(report.platform_rows_with_new_seed)}) — review them:")
        for name in report.platform_rows_with_new_seed:
            print(f"    {name}")
    if report.skipped:
        print(f"  skipped, not a namespace of strings ({len(report.skipped)}):")
        for name in report.skipped:
            print(f"    {name}")


def main(argv: Sequence[str] | None = None, *, session_factory: Callable[[], Session] = SessionLocal) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", action="store_true", help="write the rows (default: dry run)")
    parser.add_argument("--bundles-dir", type=Path, default=DEFAULT_BUNDLES_DIR)
    args = parser.parse_args(argv)
    bundles = read_bundles(args.bundles_dir)
    db = session_factory()
    try:
        report = run(db, bundles, commit=args.commit)
    finally:
        db.close()
    _print_report(report, commit=args.commit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
