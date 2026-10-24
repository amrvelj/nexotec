"""Commercial and fiscal defaults (KAN-203, FR-A-25). The row exists for
every Betrieb — written by `create_dealership`, backfilled by the KAN-203
migration — but a dealership row inserted any other way (fixtures, the
smoke seed) has none, so the read creates it with the shipped values rather
than 404 a configuration every document inherits. The write locks it.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.platform.models.dealership_settings import DealershipSettings
from app.platform.schemas.dealership_settings import DealershipSettingsUpdate

_FIELDS = (
    "currency",
    "cash_rounding",
    "default_payment_terms_days",
    "fiscal_year_start",
    "accounting_system",
    "mandant",
    "chart_of_accounts_variant",
    "period_lock_policy",
)


def _snapshot(settings: DealershipSettings) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in _FIELDS:
        value = getattr(settings, field)
        out[field] = value.value if hasattr(value, "value") else (str(value) if field == "cash_rounding" else value)
    return out


def new_settings(dealership_id: uuid.UUID, actor_id: uuid.UUID | None) -> DealershipSettings:
    """A settings row with the shipped defaults (the model's column defaults)."""

    return DealershipSettings(dealership_id=dealership_id, created_by=actor_id, updated_by=actor_id)


def settings_for(db: Session, dealership_id: uuid.UUID, *, for_update: bool = False) -> DealershipSettings:
    """The dealership's settings row, created with the shipped defaults when
    none exists yet (the KAN-393 savepoint pattern: two first requests at
    once both find no row, the loser's insert fails and it reads the
    winner's)."""

    stmt = select(DealershipSettings).where(DealershipSettings.dealership_id == dealership_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(stmt)
    if row is not None:
        return row
    db.flush()
    try:
        with db.begin_nested():
            db.add(new_settings(dealership_id, None))
    except IntegrityError:
        pass
    row = db.scalar(stmt)
    assert row is not None, "DealershipSettings row missing after its first INSERT or the concurrent winner's"
    return row


def update_settings(
    db: Session, *, settings: DealershipSettings, data: DealershipSettingsUpdate, actor_id: uuid.UUID
) -> DealershipSettings:
    changes = data.model_dump(exclude_unset=True)
    before = _snapshot(settings)
    for field, value in changes.items():
        setattr(settings, field, value)
    settings.updated_by = actor_id
    settings.version += 1
    db.flush()
    after = _snapshot(settings)
    diff_before = {k: v for k, v in before.items() if after[k] != v}
    diff_after = {k: v for k, v in after.items() if before[k] != v}
    if diff_before or diff_after:
        record_audit_event(
            db,
            entity_type="dealership_settings",
            entity_id=settings.id,
            tenant_id=settings.dealership_id,
            action="update",
            actor_id=actor_id,
            before=diff_before or None,
            after=diff_after or None,
        )
    db.commit()
    db.refresh(settings)
    return settings
