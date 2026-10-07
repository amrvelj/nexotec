"""Provider abstraction service layer (WP-5 PR-2). No provider is actually
called from here — WP-6 owns that — this is the resolution function every
future caller (WP-6's mirror sync, PR-7's migration, manual entry) goes
through so a raw provider code never reaches application code.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.errors import ConflictError, UnprocessableEntityError
from app.platform.public import get_active_reference_value_codes
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
) -> bool:
    """Admin action (PR-8): resolving a gap both marks it resolved AND
    writes the ProviderCodeMap row it was missing, so the same provider
    code never surfaces as a gap again — resolving without creating the
    mapping would just mean this exact gap reappears on the next sync.

    Validated before anything is written (KAN-77, FR-C-10): the list must be
    the gap's own `code_group` — which is also its canonical list's code, see
    `ProviderCodeMap` — and the value an active value of that list. A wrong
    mapping is never silently accepted: every later sync would trust it.

    Idempotent: resolving again with the target the code already maps to is
    a no-op (returns False, nothing written, `resolved_at` kept); a different
    target is a 409 naming the existing mapping. The same holds when the
    mapping already exists but the gap is still open (a seed migration such
    as `7c4e9a2b6d13` mapped the key later): the gap is closed against the
    existing row, never a second insert. Returns True when anything changed.

    The gap row is locked and re-read first, so two admins resolving the same
    gap at once are serialised: the second sees the first one's commit and is
    the no-op (or the 409), never a second audit row over a stale snapshot.
    An exact repeat is answered before validation, so a value deactivated
    since — or a legacy list-less code group — never turns "already done"
    into an error.
    """

    db.refresh(gap, with_for_update=True)
    if gap.resolved and canonical_list_code == gap.code_group and gap.resolved_value_code == canonical_value_code:
        return False

    if canonical_list_code != gap.code_group:
        raise UnprocessableEntityError(
            f"A '{gap.code_group}' code can only be mapped to a value of the '{gap.code_group}' reference list, "
            f"not '{canonical_list_code}'.",
            details={"codeGroup": gap.code_group, "canonicalListCode": canonical_list_code},
        )
    active = get_active_reference_value_codes(db, gap.code_group)
    if active is None:
        # Not a deployment fault here: every code group the sync writes has a
        # list, so this is an old gap from before the semantic keys. The admin
        # is told why it cannot be resolved; the gap stays open and visible.
        raise ConflictError(
            f"The code group '{gap.code_group}' has no reference list, so this gap cannot be resolved "
            "until one exists.",
            details={"codeGroup": gap.code_group},
        )
    if canonical_value_code not in active:
        raise UnprocessableEntityError(
            f"'{canonical_value_code}' is not an active value of the '{gap.code_group}' reference list.",
            details={"canonicalListCode": canonical_list_code, "canonicalValueCode": canonical_value_code},
        )

    existing = _find_code_map(db, gap)
    if existing is None:
        try:
            # Inside a savepoint (`begin_nested` flushes what is already
            # pending first, so the insert must be added in here): a row for
            # the same key committed meanwhile rolls back only this insert,
            # and the caller's transaction and `gap` stay usable.
            with db.begin_nested():
                db.add(
                    ProviderCodeMap(
                        provider=gap.provider,
                        vehicle_kind=gap.vehicle_kind,
                        code_group=gap.code_group,
                        provider_code=gap.provider_code,
                        canonical_list_code=canonical_list_code,
                        canonical_value_code=canonical_value_code,
                        created_by=actor_id,
                        updated_by=actor_id,
                    )
                )
                db.flush()
        except IntegrityError:
            # Only a code map written outside a resolve (a seed migration) can
            # collide here — the gap lock serialises resolves of this key.
            existing = _find_code_map(db, gap)
            if existing is None:
                raise

    if existing is not None and (existing.canonical_list_code, existing.canonical_value_code) != (
        canonical_list_code, canonical_value_code
    ):
        raise ConflictError(
            f"Provider code '{gap.provider_code}' is already mapped to "
            f"{existing.canonical_list_code}/{existing.canonical_value_code}.",
            details={
                "canonicalListCode": existing.canonical_list_code,
                "canonicalValueCode": existing.canonical_value_code,
            },
        )

    gap.resolved = True
    gap.resolved_at = utcnow()
    gap.resolved_value_code = canonical_value_code
    gap.resolved_by = actor_id
    db.flush()
    return True


def _find_code_map(db: Session, gap: MappingGap) -> ProviderCodeMap | None:
    return db.scalar(
        select(ProviderCodeMap).where(
            ProviderCodeMap.provider == gap.provider,
            ProviderCodeMap.vehicle_kind == gap.vehicle_kind,
            ProviderCodeMap.code_group == gap.code_group,
            ProviderCodeMap.provider_code == gap.provider_code,
        )
    )
