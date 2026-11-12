"""Give the demo provider (`auto_i_dat_mock`) the code map the real one has.

The demo adapter answers the identAPI-shaped operations from its fixtures
with auto-i's own codes (KAN-547), but the seeded `vehicle_provider_code_map`
rows name the provider `auto_i_dat` only — so on a local or staging database
every coded field the demo provider returns lands as a mapping gap, the
catalogue browse cannot translate a vehicle kind for it (KAN-548), and the
demo screens show «—» where the real provider shows labels.

This copies every `auto_i_dat` row to `auto_i_dat_mock` where the mock has
none (idempotent; a row the mock already has is left alone), which is what the
browse's vehicle-kind translation needs. The mock's fixtures carry auto-i's
own codes as the identAPI sends them (KAN-550: vehicle kind `01`, gearbox
`1`/`5`), so the demo variants' coded fields resolve once this ran.
Then every open mapping gap of the demo provider whose key is now mapped is
resolved to that mapping (KAN-649) through the production path,
`catalogue_admin.resolve_gap`: the gap is closed against the existing row and
the variants, provider configurations, options, colours and tyres it left
NULL are filled, with the resolve's audit row (no actor: a system resolve,
its reason `REASON`). A gap whose code is still unmapped, or whose mapping
the resolve refuses (a value deactivated since), stays open and is counted.

Vehicle context only; idempotent. Run it once per local or staging database,
and again after every deploy whose migration seeds new `auto_i_dat`
mappings (KAN-587's size classes, say):

    python scripts/seed_demo_provider_code_map.py

Not a migration on purpose: the mock is demo data, and production never
carries a mock connection (KAN-489).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, UnprocessableEntityError
from app.db import SessionLocal
from app.vehicle.models.provider import MappingGap, ProviderCodeMap
from app.vehicle.services import catalogue_admin

REAL_PROVIDER = "auto_i_dat"
DEMO_PROVIDER = "auto_i_dat_mock"
REASON = "kan649_demo_provider_code_map_seeded"


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
# values themselves (`exterior`, `comfort`, …) under its vehicle kind `01`
# (KAN-550; `1` before, the SOAP-era kind), where the real provider sends its
# own group codes. Since the details load
# through the code map (FR-C-10) the demo needs these identity rows, or every
# demo option is a mapping gap and a flat list.
DEMO_OPTION_GROUPS = ("exterior", "interior", "comfort", "infotainment", "safety")


def seed_demo_option_groups(db: Session) -> int:
    existing = {
        row.provider_code
        for row in db.scalars(
            select(ProviderCodeMap).where(
                ProviderCodeMap.provider == DEMO_PROVIDER, ProviderCodeMap.code_group == "option_group",
                ProviderCodeMap.vehicle_kind == "01",
            )
        )
    }
    added = 0
    for code in DEMO_OPTION_GROUPS:
        if code in existing:
            continue
        db.add(
            ProviderCodeMap(
                provider=DEMO_PROVIDER, vehicle_kind="01", code_group="option_group", provider_code=code,
                canonical_list_code="option_group", canonical_value_code=code,
            )
        )
        added += 1
    db.commit()
    return added


def resolve_mapped_demo_gaps(db: Session) -> tuple[int, int]:
    """Resolves every open gap of the demo provider whose key the code map
    now holds, to that mapping (KAN-649). Returns (resolved, refused): a gap
    `resolve_gap` refuses is rolled back alone and stays open. Each resolve
    commits on its own, as an admin's does."""

    open_gaps = db.scalars(
        select(MappingGap)
        .where(MappingGap.provider == DEMO_PROVIDER, MappingGap.resolved.is_(False))
        .order_by(MappingGap.code_group, MappingGap.provider_code)
    ).all()
    mapped = {
        (row.vehicle_kind, row.code_group, row.provider_code): row
        for row in db.scalars(select(ProviderCodeMap).where(ProviderCodeMap.provider == DEMO_PROVIDER))
    }
    targets = [
        (gap.id, mapped[(gap.vehicle_kind, gap.code_group, gap.provider_code)])
        for gap in open_gaps
        if (gap.vehicle_kind, gap.code_group, gap.provider_code) in mapped
    ]
    resolved = refused = 0
    for gap_id, mapping in targets:
        gap = db.get(MappingGap, gap_id)
        if gap is None:
            continue
        try:
            catalogue_admin.resolve_gap(
                db, gap=gap, canonical_list_code=mapping.canonical_list_code,
                canonical_value_code=mapping.canonical_value_code, actor_id=None, reason=REASON,
            )
        except (ConflictError, UnprocessableEntityError) as exc:
            db.rollback()
            refused += 1
            print(f"gap {gap_id} ({mapping.code_group} {mapping.provider_code}) left open: {exc}")
            continue
        resolved += 1
    return resolved, refused


def main() -> None:
    db = SessionLocal()
    try:
        added = copy_code_map(db) + seed_demo_option_groups(db)
        resolved, refused = resolve_mapped_demo_gaps(db)
    finally:
        db.close()
    print(f"demo provider code map: {added} row(s) added for {DEMO_PROVIDER}.")
    print(f"demo provider gaps: {resolved} resolved to their mapping, {refused} left open (refused).")


if __name__ == "__main__":
    main()
