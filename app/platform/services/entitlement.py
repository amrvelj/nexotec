"""Module entitlements (KAN-198, D-A-04): what a Betrieb is licensed for.

Written only by platform_admin (`set_entitlements`, a full replacement of the
set), read by the session minting path (`list_active_modules`) and by the
core gate through the version reader this module registers at import:
app.core.permissions.require_entitlement compares the token's `ent_v` with
`dealership.entitlements_version`, and core cannot import platform, so the
read arrives as a callback. Importing this module is what registers it; the
dealerships router imports it, so the API process always has it.
"""

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.permissions import ModuleCode, set_entitlement_version_reader
from app.platform.models.dealership import Dealership
from app.platform.models.module_entitlement import ModuleEntitlement


def _rows(db: Session, dealership_id: uuid.UUID) -> list[ModuleEntitlement]:
    return list(
        db.scalars(
            select(ModuleEntitlement)
            .where(ModuleEntitlement.dealership_id == dealership_id)
            .order_by(ModuleEntitlement.module)
        ).all()
    )


def list_active_modules(db: Session, dealership_id: uuid.UUID, *, at: dt.datetime | None = None) -> frozenset[str]:
    """The module codes the dealership holds right now — what the token carries."""

    now = at or utcnow()
    return frozenset(row.module.value for row in _rows(db, dealership_id) if row.is_active(now))


def read_entitlements_version(db: Session, dealership_id: uuid.UUID) -> int | None:
    """The gate's staleness check: None when no such dealership exists."""

    return db.scalar(select(Dealership.entitlements_version).where(Dealership.id == dealership_id))


set_entitlement_version_reader(read_entitlements_version)


def set_entitlements(
    db: Session, *, dealership: Dealership, modules: set[ModuleCode], actor_id: uuid.UUID
) -> frozenset[str]:
    """Replace the dealership's entitlement set (platform_admin only — the
    API checks). Ends the rows no longer wanted (valid_to), reopens or adds
    the wanted ones, audits before/after, and bumps `entitlements_version`
    so every session minted before this change is refused on its next
    gated request and signs in again. Returns the active set."""

    now = utcnow()
    wanted = {module.value for module in modules}
    rows = {row.module.value: row for row in _rows(db, dealership.id)}
    before = sorted(code for code, row in rows.items() if row.is_active(now))

    for code, row in rows.items():
        if code in wanted and not row.is_active(now):
            row.valid_from = now
            row.valid_to = None
            row.granted_at = now
            row.granted_by = actor_id
        elif code not in wanted and row.is_active(now):
            row.valid_to = now
    for code in wanted - set(rows):
        db.add(
            ModuleEntitlement(
                dealership_id=dealership.id,
                module=ModuleCode(code),
                granted_at=now,
                granted_by=actor_id,
                valid_from=now,
                valid_to=None,
            )
        )

    after = sorted(wanted)
    if after != before:
        dealership.entitlements_version += 1
        dealership.version += 1
        dealership.updated_by = actor_id
        record_audit_event(
            db,
            entity_type="dealership",
            entity_id=dealership.id,
            tenant_id=dealership.id,
            action="entitlements_set",
            actor_id=actor_id,
            before={"modules": before},
            after={"modules": after},
        )
    db.commit()
    db.refresh(dealership)
    return frozenset(after)
