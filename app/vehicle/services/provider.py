"""Provider abstraction service layer (WP-5 PR-2). No provider is actually
called from here — WP-6 owns that — this is the resolution function every
future caller (WP-6's mirror sync, PR-7's migration, manual entry) goes
through so a raw provider code never reaches application code.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.vehicle.models.provider import MappingGap, ProviderCodeMap


@dataclass(frozen=True)
class CanonicalValue:
    list_code: str
    value_code: str


def resolve_provider_code(
    db: Session, *, provider: str, vehicle_kind: str, code_group: str, provider_code: str
) -> CanonicalValue | None:
    """Returns the canonical value this provider code maps to for this
    vehicle_kind, or None if unmapped — in which case a MappingGap row has
    already been written (created on first miss, occurrences/last_seen_at
    bumped on every later miss of the exact same code), never silently
    dropped.
    """

    mapping = db.scalar(
        select(ProviderCodeMap).where(
            ProviderCodeMap.provider == provider,
            ProviderCodeMap.vehicle_kind == vehicle_kind,
            ProviderCodeMap.code_group == code_group,
            ProviderCodeMap.provider_code == provider_code,
        )
    )
    if mapping is not None:
        return CanonicalValue(list_code=mapping.canonical_list_code, value_code=mapping.canonical_value_code)

    _record_mapping_gap(db, provider=provider, vehicle_kind=vehicle_kind, code_group=code_group, provider_code=provider_code)
    return None


def _record_mapping_gap(
    db: Session, *, provider: str, vehicle_kind: str, code_group: str, provider_code: str
) -> MappingGap:
    gap = db.scalar(
        select(MappingGap).where(
            MappingGap.provider == provider,
            MappingGap.vehicle_kind == vehicle_kind,
            MappingGap.code_group == code_group,
            MappingGap.provider_code == provider_code,
        )
    )
    if gap is not None:
        gap.occurrences += 1
        gap.last_seen_at = utcnow()
        db.flush()
        return gap

    gap = MappingGap(
        provider=provider,
        vehicle_kind=vehicle_kind,
        code_group=code_group,
        provider_code=provider_code,
    )
    db.add(gap)
    db.flush()
    return gap


def resolve_mapping_gap(
    db: Session, *, gap: MappingGap, canonical_list_code: str, canonical_value_code: str, actor_id: uuid.UUID
) -> MappingGap:
    """Admin action (PR-8): resolving a gap both marks it resolved AND
    writes the ProviderCodeMap row it was missing, so the same provider
    code never surfaces as a gap again — resolving without creating the
    mapping would just mean this exact gap reappears on the next sync.
    """

    db.add(
        ProviderCodeMap(
            provider=gap.provider,
            vehicle_kind=gap.vehicle_kind,
            code_group=gap.code_group,
            provider_code=gap.provider_code,
            canonical_list_code=canonical_list_code,
            canonical_value_code=canonical_value_code,
        )
    )
    gap.resolved = True
    gap.resolved_at = utcnow()
    gap.resolved_value_code = canonical_value_code
    gap.resolved_by = actor_id
    db.flush()
    return gap
