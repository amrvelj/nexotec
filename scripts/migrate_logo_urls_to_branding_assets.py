"""Move every `dealership.logo_url` (the WP-6b external reference) into the
object store as that Betrieb's `primary` branding asset (KAN-205; exit
criterion: "existing values become primary assets where fetchable, else
reported").

A migration makes no network call, so this is a script, run by hand once per
environment after `alembic upgrade heads` has applied 444ddb84d8c9:

    python scripts/migrate_logo_urls_to_branding_assets.py            # dry run: the report only
    python scripts/migrate_logo_urls_to_branding_assets.py --commit   # writes

It needs the environment's real object store: the local directory store is
refused in production (KAN-402 brings the bucket), so on production it runs
once that store exists; on staging and locally it runs as is.

For each Betrieb with a `logo_url`, one line of the report:
- `migrated` / `would migrate` — fetched, and accepted by the upload's own
  validation (`logo_validation.validate_logo`: SVG sanitised, PNG ≥ 600 px,
  JPEG refused). With `--commit` the file is stored through the upload's own
  write path (`branding.set_asset`: object store, `branding_asset` row,
  version bump, audit row `branding_asset_set` with this script's reason, no
  actor) and `logo_url` is cleared **in the same commit** — the asset is now
  the one fact.
- `rejected <reason>` — fetched but refused by that validation (the same
  `details.reason` the upload gives, e.g. `logo_format_jpeg`,
  `logo_too_small`); `logo_url` stays for a person to look at, and documents
  print the initials mark until the manager uploads a logo that passes — the
  renderer never fetches an external URL (KAN-205 review: it loads `data:`
  URIs only), so a `logo_url` alone prints nothing.
- `unfetchable <why>` — not http(s), the request failed, the answer was not
  200, or the body exceeded the upload's size cap; `logo_url` stays.
- `skipped: has a primary logo` — a Betrieb that already uploaded one; its
  `logo_url` is left for a person to look at (the renderer prints the asset).

Idempotent: a migrated Betrieb has no `logo_url` left, so a second run finds
nothing to do. Exits 1 when any `logo_url` remains after the run (or would,
for a dry run), 0 otherwise. No outbox event: nothing consumes branding.

The dry run fetches and validates but stores nothing — each Betrieb's row is
independent of every other (one `primary` per Betrieb), so no conflict
between rows of one pass exists for a rolled-back real run to find.
"""

import argparse
import sys
from collections.abc import Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableEntityError
from app.core.object_store import ObjectStore, get_object_store
from app.db import SessionLocal
from app.platform.models.branding_asset import BrandingAssetKind
from app.platform.models.dealership import Dealership
from app.platform.services import branding
from app.platform.services.logo_validation import MAX_BYTES, validate_logo

REASON = "KAN-205: logo_url moved into the object store by scripts/migrate_logo_urls_to_branding_assets.py"

Fetcher = Callable[[str], bytes]


class Unfetchable(Exception):
    pass


def fetch(url: str) -> bytes:
    if not url.lower().startswith(("https://", "http://")):
        raise Unfetchable("not an http(s) URL")
    try:
        with (
            httpx.Client(timeout=10.0, follow_redirects=True, max_redirects=3) as client,
            client.stream("GET", url) as response,
        ):
            if response.status_code != 200:
                raise Unfetchable(f"HTTP {response.status_code}")
            body = b""
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise Unfetchable(f"larger than {MAX_BYTES} bytes")
            return body
    except httpx.HTTPError as exc:
        raise Unfetchable(type(exc).__name__) from None


def migrate(db: Session, store: ObjectStore, *, commit: bool, fetcher: Fetcher = fetch) -> list[tuple[Dealership, str]]:
    """One (dealership, outcome) per Betrieb that had a `logo_url`."""

    rows = db.scalars(select(Dealership).where(Dealership.logo_url.is_not(None)).order_by(Dealership.id)).all()
    report: list[tuple[Dealership, str]] = []
    for row in rows:
        dealership_id = row.id
        if BrandingAssetKind.PRIMARY in branding.list_assets(db, dealership_id):
            report.append((row, "skipped: has a primary logo"))
            continue
        url = row.logo_url or ""
        try:
            data = fetcher(url)
        except Unfetchable as exc:
            report.append((row, f"unfetchable {exc}"))
            continue
        try:
            if not commit:
                validate_logo(data)
                report.append((row, "would migrate"))
                continue
            locked = db.scalar(select(Dealership).where(Dealership.id == dealership_id).with_for_update())
            assert locked is not None
            locked.logo_url = None  # commits with the asset, in set_asset's one transaction
            branding.set_asset(
                db, store, dealership=locked, kind=BrandingAssetKind.PRIMARY, data=data, actor_id=None, reason=REASON
            )
            report.append((locked, "migrated"))
        except UnprocessableEntityError as exc:
            db.rollback()
            report.append((row, f"rejected {(exc.details or {}).get('reason', 'invalid')}"))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", action="store_true", help="store the logos and clear logo_url (default: report only)")
    args = parser.parse_args(argv)
    db = SessionLocal()
    try:
        report = migrate(db, get_object_store(), commit=args.commit)
        for dealership, outcome in report:
            print(f"KAN-205 REPORT: {dealership.legal_name} ({dealership.id}): {outcome}")
        remaining = sum(1 for _d, outcome in report if outcome not in ("migrated",))
        print(f"KAN-205: {len(report)} Betrieb(e) with a logo_url; {remaining} still hold one after this run")
        return 1 if remaining else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
