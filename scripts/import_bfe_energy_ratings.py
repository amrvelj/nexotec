"""BFE (Bundesamt für Energie) energy/emission import (WP-5 PR-8, ADR-042).

Platform-wide, not per-tenant — this is public Swiss data, not a licensed
provider feed (unlike auto-i-dat, PR-2). No live BFE API integration
exists in this sandbox; this script imports from a CSV export matching
BFE's published dataset shape (model_variant_code, rating_year,
energy_efficiency_category, emission_standard, consumption_norm) — one
row per variant per year. Re-running the same file is idempotent:
upsert_energy_rating() replaces an existing (variant, year) row rather
than duplicating it.

Note honestly, per this package's own "framework exists, cron doesn't yet"
pattern (same gap as app.reconciliation_runner and the nightly
reconciliation job it's never actually scheduled to run): there is no
Integrations-registry job wired to run this automatically. Running it is,
for now, a manual/scheduled-externally action, same category of gap as
the rest of this project's scheduling story — not something this package
silently invents a scheduler to paper over.

A model_variant_code not found in the catalogue is skipped and reported,
never silently guessed at. A row with no energy_efficiency_category value
is imported as-is (None) rather than defaulted — a vehicle the BFE dataset
doesn't cover for a given field genuinely has no value for it.

Usage:
    DMS_DATABASE_URL=... python scripts/import_bfe_energy_ratings.py ratings.csv
"""

import csv
import dataclasses
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.vehicle.models.catalogue import ModelVariant
from app.vehicle.models.provider import ProviderEntityRef
from app.vehicle.services.energy_rating import upsert_energy_rating


@dataclasses.dataclass
class ImportSummary:
    total_rows: int
    imported: int
    skipped_unknown_variant: list[str]


def import_bfe_ratings(db: Session, rows: list[dict]) -> ImportSummary:
    imported = 0
    skipped: list[str] = []

    for row in rows:
        variant_code = row["model_variant_code"]
        # ModelVariant has no separate "code" column of its own (PR-1) — it
        # is looked up via its ProviderEntityRef (PR-2) under a "bfe"
        # provider key, the same cross-reference mechanism used for any
        # other external identifier.
        ref = db.scalar(
            select(ProviderEntityRef).where(
                ProviderEntityRef.provider == "bfe", ProviderEntityRef.provider_key == variant_code
            )
        )
        if ref is None:
            skipped.append(variant_code)
            continue

        variant = db.get(ModelVariant, ref.entity_id)
        if variant is None:
            skipped.append(variant_code)
            continue

        upsert_energy_rating(
            db,
            model_variant_id=variant.id,
            rating_year=int(row["rating_year"]),
            energy_efficiency_category=row.get("energy_efficiency_category") or None,
            emission_standard=row.get("emission_standard") or None,
            consumption_norm=row.get("consumption_norm") or None,
        )
        imported += 1

    db.commit()
    return ImportSummary(total_rows=len(rows), imported=imported, skipped_unknown_variant=skipped)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("Usage: python scripts/import_bfe_energy_ratings.py <ratings.csv>")
        return 1

    with open(argv[0], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    db = SessionLocal()
    try:
        summary = import_bfe_ratings(db, rows)
    finally:
        db.close()

    print(f"total rows: {summary.total_rows}")
    print(f"imported: {summary.imported}")
    if summary.skipped_unknown_variant:
        print(f"skipped (no matching catalogue variant): {len(summary.skipped_unknown_variant)}")
        for code in summary.skipped_unknown_variant:
            print(f"  {code}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
