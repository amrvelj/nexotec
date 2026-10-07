"""Reads and writes of the plate-lookup cache (FR-C-02, C-D / KAN-42).

**TTL: 30 days** (`PLATE_LOOKUP_CACHE_TTL`). A `KontrollschildInfo` answer
changes only when a plate is reassigned or a car is re-registered, both
rare against a 30-day window, while advisors retype the same plate many
times in one negotiation and every call is billed. A stale row costs one
wrong-looking picker the advisor can step past by typing the Typenschein;
a short TTL costs money on every retype. Rows past the TTL are never read
and are deleted by the daily `vehicle.plate_lookup_cache.purge` job, so the
table never holds plate → Stammnummer pairs longer than it needs to
(revDSG data minimisation, PRD-Configurator §Data protection).

**Never enumerable.** Every public function here takes an exact `plate` or
an exact `stammnummer` plus the `tenant_id`; there is deliberately no
function that lists, pages or exports rows.
`tests/architecture/test_plate_lookup_is_not_enumerable.py` asserts it.
"""

import datetime as dt
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.integration.public import PlateInfoData
from app.vehicle.models.plate_lookup_cache import PlateLookupCacheEntry

PLATE_LOOKUP_CACHE_TTL = dt.timedelta(days=30)


def _fresh_after(now: dt.datetime) -> dt.datetime:
    return now - PLATE_LOOKUP_CACHE_TTL


def _distinct(rows) -> list[PlateInfoData]:
    """Two concurrent first lookups of one plate can both store the answer;
    the same record twice must never read as a Wechselschild."""

    return list(dict.fromkeys(_to_record(row) for row in rows))


def _to_record(row: PlateLookupCacheEntry) -> PlateInfoData:
    return PlateInfoData(
        vehicle_kind_code=row.vehicle_kind_code,
        brand_name=row.brand_name,
        model_description=row.model_description,
        production_from=row.production_from,
        production_to=row.production_to,
        type_approval_number=row.type_approval_number,
        first_registration_date=row.first_registration_date,
        stammnummer=row.stammnummer,
    )


def cached_records_for_plate(
    db: Session, *, tenant_id: uuid.UUID, plate: str, now: dt.datetime | None = None
) -> list[PlateInfoData] | None:
    """The fresh cached answer for exactly this plate, or `None` when there
    is none. An empty list is never returned: a provider miss is not cached,
    because the next lookup may well be the one that finds a newly
    registered car.
    """

    rows = db.scalars(
        select(PlateLookupCacheEntry)
        .where(
            PlateLookupCacheEntry.tenant_id == tenant_id,
            PlateLookupCacheEntry.plate == plate,
            PlateLookupCacheEntry.fetched_at > _fresh_after(now or utcnow()),
        )
        .order_by(PlateLookupCacheEntry.stammnummer, PlateLookupCacheEntry.id)
    ).all()
    if not rows:
        return None
    return _distinct(rows)


def cached_records_for_stammnummer(
    db: Session, *, tenant_id: uuid.UUID, stammnummer: str, now: dt.datetime | None = None
) -> list[PlateInfoData]:
    """FR-C-02 step 3: auto-i-dat accepts no Stammnummer search, so a
    Stammnummer resolves only through an earlier plate lookup's answer."""

    rows = db.scalars(
        select(PlateLookupCacheEntry)
        .where(
            PlateLookupCacheEntry.tenant_id == tenant_id,
            PlateLookupCacheEntry.stammnummer == stammnummer,
            PlateLookupCacheEntry.fetched_at > _fresh_after(now or utcnow()),
        )
        .order_by(PlateLookupCacheEntry.fetched_at.desc(), PlateLookupCacheEntry.id)
    ).all()
    return _distinct(rows)


def store_records_for_plate(
    db: Session, *, tenant_id: uuid.UUID, plate: str, records: list[PlateInfoData], now: dt.datetime | None = None
) -> None:
    """Replaces every row held for this plate with the provider's latest
    answer. Flushes; the caller owns the transaction."""

    fetched_at = now or utcnow()
    db.execute(
        delete(PlateLookupCacheEntry).where(
            PlateLookupCacheEntry.tenant_id == tenant_id, PlateLookupCacheEntry.plate == plate
        )
    )
    for record in records:
        db.add(
            PlateLookupCacheEntry(
                tenant_id=tenant_id,
                plate=plate,
                vehicle_kind_code=record.vehicle_kind_code,
                brand_name=record.brand_name,
                model_description=record.model_description,
                production_from=record.production_from,
                production_to=record.production_to,
                type_approval_number=record.type_approval_number,
                first_registration_date=record.first_registration_date,
                stammnummer=record.stammnummer,
                fetched_at=fetched_at,
            )
        )
    db.flush()


def purge_expired_plate_lookups(db: Session, *, now: dt.datetime | None = None) -> int:
    """Daily job: deletes every row past the TTL, across tenants. Returns
    the number of rows deleted. Deletes only — it reads nothing back."""

    result = db.execute(
        delete(PlateLookupCacheEntry).where(PlateLookupCacheEntry.fetched_at <= _fresh_after(now or utcnow()))
    )
    db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
