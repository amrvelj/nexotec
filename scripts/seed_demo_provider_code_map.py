"""Give the demo provider (`auto_i_dat_mock`) the code map the real one has.

The demo adapter answers the identAPI-shaped operations from its fixtures
with auto-i's own codes (KAN-547), but the seeded `vehicle_provider_code_map`
rows name the provider `auto_i_dat` only — so on a local or staging database
every coded field the demo provider returns lands as a mapping gap, the
catalogue browse cannot translate a vehicle kind for it (KAN-548), and the
demo screens show «—» where the real provider shows labels.

This copies every `auto_i_dat` row to `auto_i_dat_mock` where the mock has
none (idempotent; a row the mock already has is left alone), which is what the
browse's vehicle-kind translation needs. The mock's fixtures still carry some
SOAP-era codes the identAPI never sends (vehicle kind `1`, gearbox `A`/`M`),
and those stay mapping gaps until KAN-550 retires the SOAP shapes — the demo
shows «—» for them, as before. Vehicle context only; run once per local or
staging database:

    python scripts/seed_demo_provider_code_map.py

Not a migration on purpose: the mock is demo data, and production never
carries a mock connection (KAN-489).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.vehicle.models.provider import ProviderCodeMap

REAL_PROVIDER = "auto_i_dat"
DEMO_PROVIDER = "auto_i_dat_mock"


def copy_code_map(db: Session) -> int:
    """Returns how many rows were added for the demo provider."""

    real_rows = db.scalars(select(ProviderCodeMap).where(ProviderCodeMap.provider == REAL_PROVIDER)).all()
    existing = {
        (row.vehicle_kind, row.code_group, row.provider_code)
        for row in db.scalars(select(ProviderCodeMap).where(ProviderCodeMap.provider == DEMO_PROVIDER)).all()
    }
    added = 0
    for row in real_rows:
        key = (row.vehicle_kind, row.code_group, row.provider_code)
        if key in existing:
            continue
        db.add(
            ProviderCodeMap(
                provider=DEMO_PROVIDER,
                vehicle_kind=row.vehicle_kind,
                code_group=row.code_group,
                provider_code=row.provider_code,
                canonical_list_code=row.canonical_list_code,
                canonical_value_code=row.canonical_value_code,
            )
        )
        added += 1
    db.commit()
    return added


# KAN-211: the demo adapter's option groups are the canonical `option_group`
# values themselves (`exterior`, `comfort`, …) under its vehicle kind `1`,
# where the real provider sends CodeGrpNr 046 codes. Since the details load
# through the code map (FR-C-10) the demo needs these identity rows, or every
# demo option is a mapping gap and a flat list.
DEMO_OPTION_GROUPS = ("exterior", "interior", "comfort", "infotainment", "safety")


def seed_demo_option_groups(db: Session) -> int:
    existing = {
        row.provider_code
        for row in db.scalars(
            select(ProviderCodeMap).where(
                ProviderCodeMap.provider == DEMO_PROVIDER, ProviderCodeMap.code_group == "option_group",
                ProviderCodeMap.vehicle_kind == "1",
            )
        )
    }
    added = 0
    for code in DEMO_OPTION_GROUPS:
        if code in existing:
            continue
        db.add(
            ProviderCodeMap(
                provider=DEMO_PROVIDER, vehicle_kind="1", code_group="option_group", provider_code=code,
                canonical_list_code="option_group", canonical_value_code=code,
            )
        )
        added += 1
    db.commit()
    return added


def main() -> None:
    db = SessionLocal()
    try:
        added = copy_code_map(db) + seed_demo_option_groups(db)
    finally:
        db.close()
    print(f"demo provider code map: {added} row(s) added for {DEMO_PROVIDER}.")


if __name__ == "__main__":
    main()
