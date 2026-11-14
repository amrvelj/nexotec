"""Commercial and fiscal defaults (KAN-203, FR-A-25). The row exists for
every Betrieb — written by `create_dealership`, backfilled by the KAN-203
migration — but a dealership row inserted any other way (fixtures, the
smoke seed) has none, so the read answers the shipped values without
writing, and the first write creates the row (locked, the KAN-393 pattern).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.config import get_settings
from app.platform.organisation.models.dealership_settings import (
    SHIPPED_ADDRESS_AUTOCOMPLETE_ENABLED,
    SHIPPED_CASH_ROUNDING,
    SHIPPED_CURRENCY,
    SHIPPED_FISCAL_YEAR_START,
    SHIPPED_PAYMENT_TERMS_DAYS,
    ChartOfAccountsVariant,
    DealershipSettings,
    PeriodLockPolicy,
)
from app.platform.organisation.schemas.dealership_settings import DealershipSettingsUpdate

_FIELDS = (
    "currency",
    "cash_rounding",
    "default_payment_terms_days",
    "fiscal_year_start",
    "accounting_system",
    "mandant",
    "chart_of_accounts_variant",
    "period_lock_policy",
    "address_autocomplete_enabled",
)


def _snapshot(settings: DealershipSettings) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in _FIELDS:
        value = getattr(settings, field)
        out[field] = value.value if hasattr(value, "value") else (str(value) if field == "cash_rounding" else value)
    return out


def new_settings(dealership_id: uuid.UUID, actor_id: uuid.UUID | None) -> DealershipSettings:
    """A settings row with the shipped defaults (the model's column defaults)."""

    # Set explicitly, not left to the column defaults (those apply at flush;
    # the transient row `read_settings` answers with is never flushed).
    return DealershipSettings(
        dealership_id=dealership_id,
        currency=SHIPPED_CURRENCY,
        cash_rounding=SHIPPED_CASH_ROUNDING,
        default_payment_terms_days=SHIPPED_PAYMENT_TERMS_DAYS,
        fiscal_year_start=SHIPPED_FISCAL_YEAR_START,
        chart_of_accounts_variant=ChartOfAccountsVariant.KMU,
        period_lock_policy=PeriodLockPolicy.MONTH_END_CLOSE,
        address_autocomplete_enabled=SHIPPED_ADDRESS_AUTOCOMPLETE_ENABLED,
        created_by=actor_id,
        updated_by=actor_id,
    )


def read_settings(db: Session, dealership_id: uuid.UUID) -> DealershipSettings:
    """The row to READ: the stored one, or — for a dealership row inserted
    around the service, which has none — a transient row with the shipped
    values (version 1, the version the first PATCH will create it with).
    A read never inserts, the auditor's included."""

    row = db.scalar(select(DealershipSettings).where(DealershipSettings.dealership_id == dealership_id))
    if row is not None:
        return row
    transient = new_settings(dealership_id, None)
    transient.version = 1
    transient.created_at = transient.updated_at = utcnow()
    return transient


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


def address_autocomplete_config(db: Session, dealership_id: uuid.UUID) -> tuple[bool, str | None]:
    """KAN-37 (ADR-101): `(enabled, browser_key)` for the SPA's address
    forms. Enabled only when the Betrieb's switch is on and the platform holds
    a key (`DMS_GOOGLE_PLACES_BROWSER_KEY`); the key travels only then. A
    read, through `read_settings`, so a Betrieb without a settings row
    answers the shipped value (on) without a write."""

    key = get_settings().google_places_browser_key or None
    enabled = bool(key) and read_settings(db, dealership_id).address_autocomplete_enabled
    return enabled, (key if enabled else None)
