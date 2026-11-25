"""The settings store in Dealer Administration (KAN-805 PR-2, ADR-214,
ADR-216): the areas a Betrieb configures, one area's settings, a value
written for the Betrieb or a location, a setting's history, a restore.

Nested under /dealerships/{dealership_id}: tenant from the token
(`require_tenant_match`, 404-not-403 on another Betrieb's id); the platform
tenant is not found (`get_dealer_dealership_or_404`). The matrix row
«Dealership settings»: every role of the Betrieb reads — the values and,
by Anto's ruling of 2026-11-24, the history — and only the manager (or
platform staff, as the row says) writes. A setting of a module the Betrieb
does not hold is not found (404) to whoever may call the route; the role
check runs first, so a non-manager's write is 403 whatever the key.

A write is a PUT of the slot's whole value with If-Match on the slot's
version (0 for a slot never written); `{"value": null}` clears it back to
«not set». A restore is a POST with If-Match on the slot it restores into,
and honours an Idempotency-Key (`IdempotentRoute`). A value that does not fit
its declaration is 422 with its reason; a deactivated location's value is
409 `setting_location_inactive`.
"""

import re
import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.concurrency import require_if_match
from app.core.config import get_settings
from app.core.errors import ConflictError, UnprocessableEntityError
from app.core.idempotent_route import IdempotentRoute
from app.core.pagination import SortPageParams
from app.core.permissions import require_read, require_write
from app.core.sorting import decode_cursor_for_sort
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.administration.schemas.area_settings import (
    AreaSettingRead,
    AreaSettingsRead,
    SettingAreaList,
    SettingAreaRead,
    SettingChangePage,
    SettingChangeRead,
    SettingDeclarationRead,
    SettingLocationRead,
    SettingSlotRead,
    SettingValueBody,
    SettingWrite,
    TextValueBody,
)
from app.platform.administration.services import area_settings as area_service
from app.platform.organisation.services import setting_store as store
from app.platform.organisation.services.dealership import get_dealer_dealership_or_404
from app.platform.organisation.services.setting_declarations import (
    SettingDeclaration,
    SettingKind,
    SettingValueError,
    TextValue,
)

router = APIRouter(tags=["settings-store"], route_class=IdempotentRoute)
settings = get_settings()

_PLAIN_DECIMAL = re.compile(r"-?[0-9]{1,30}(\.[0-9]{1,10})?", re.ASCII)

_KIND_FIELD = {
    SettingKind.SWITCH: "switch",
    SettingKind.NUMBER: "number",
    SettingKind.AMOUNT: "number",
    SettingKind.CHOICE: "choice",
    SettingKind.TEXT: "text",
}


def _body_of(value: Any) -> SettingValueBody | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return SettingValueBody(switch=value)
    if isinstance(value, TextValue):
        return SettingValueBody(text=TextValueBody(name=value.name, translations=dict(value.translations)))
    if isinstance(value, str):
        return SettingValueBody(choice=value)
    return SettingValueBody(number=str(value))


def _value_of_body(declaration: SettingDeclaration, body: SettingValueBody | None) -> Any:
    """The Python value a write names, or NOT_SET; the field must be the kind's."""

    if body is None:
        return store.NOT_SET
    field = _KIND_FIELD[SettingKind(declaration.kind)]
    given = [name for name in ("switch", "number", "choice", "text") if getattr(body, name) is not None]
    if given != [field]:
        raise SettingValueError(
            f"{declaration.key} takes its value in `{field}` only.", reason="setting_value_wrong_field"
        )
    if field == "text":
        assert body.text is not None
        return TextValue(name=body.text.name, translations=tuple(sorted(body.text.translations.items())))
    if field == "number":
        # A plain decimal only: digits, one optional point, an optional minus. Python's
        # Decimal() would also take spaces, «_», «+», exponents and non-ASCII digits.
        text = body.number or ""
        if not _PLAIN_DECIMAL.fullmatch(text):
            raise SettingValueError(f"{declaration.key} takes a number.", reason="setting_value_not_a_number")
        return Decimal(text) + 0  # «-0» is 0
    return getattr(body, field)


def _declaration_read(declaration: SettingDeclaration) -> SettingDeclarationRead:
    return SettingDeclarationRead(
        key=declaration.key,
        area=declaration.area,
        module=declaration.module,
        kind=str(declaration.kind),
        when_not_set=str(declaration.when_not_set),
        acts_as=_body_of(declaration.acts_as),
        minimum=declaration.minimum,
        maximum=declaration.maximum,
        decimals=declaration.decimals,
        unit=declaration.unit,
        choices=list(declaration.choices),
        checklist_item=declaration.checklist_item,
        checklist_blocks=declaration.checklist_blocks,
    )


def _slot_read(slot: store.SettingSlot) -> SettingSlotRead:
    return SettingSlotRead(
        location_id=slot.location_id,
        is_set=slot.is_set,
        value=_body_of(slot.value),
        version=slot.version,
        changed_at=slot.changed_at,
        changed_by=slot.changed_by,
        fits_declaration=slot.fits_declaration,
    )


def _change_read(change: store.SettingChange) -> SettingChangeRead:
    return SettingChangeRead(
        id=change.id,
        key=change.key,
        location_id=change.location_id,
        origin=change.origin.value,
        is_set=change.is_set,
        value=_body_of(change.value),
        version_after=change.version_after,
        restored_from_id=change.restored_from_id,
        created_at=change.created_at,
        created_by=change.created_by,
    )


def _refusals(call):
    """Runs a store write and answers its refusals: 422 for a value that does
    not fit, 409 for one the store refuses."""

    try:
        return call()
    except SettingValueError as value_error:
        raise UnprocessableEntityError(str(value_error), details={"reason": value_error.reason}) from value_error
    except store.SettingRefused as refused:
        raise ConflictError(str(refused), details={"reason": refused.reason}) from refused


def _guard(db: Session, dealership_id: uuid.UUID, principal: Principal) -> None:
    require_tenant_match(dealership_id, principal)
    get_dealer_dealership_or_404(db, dealership_id)


@router.get("/dealerships/{dealership_id}/setting-areas", response_model=SettingAreaList)
def list_setting_areas(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_settings")),
    db: Session = Depends(get_db),
):
    _guard(db, dealership_id, principal)
    areas = area_service.list_areas(db, dealership_id=dealership_id)
    return SettingAreaList(items=[SettingAreaRead(area=a.area, setting_count=a.setting_count) for a in areas])


@router.get("/dealerships/{dealership_id}/setting-areas/{area}", response_model=AreaSettingsRead)
def get_setting_area(
    dealership_id: uuid.UUID,
    area: str,
    principal: Principal = Depends(require_read("dealership_settings")),
    db: Session = Depends(get_db),
):
    _guard(db, dealership_id, principal)
    view = area_service.area_view(db, dealership_id=dealership_id, area=area)
    return AreaSettingsRead(
        area=view.area,
        shows_locations=view.shows_locations,
        locations=[SettingLocationRead(id=loc.id, name=loc.name, is_active=loc.is_active) for loc in view.locations],
        settings=[
            AreaSettingRead(
                declaration=_declaration_read(item.declaration),
                dealership=_slot_read(item.dealership),
                locations=[_slot_read(slot) for slot in item.locations],
            )
            for item in view.settings
        ],
    )


def _write(
    db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID | None, key: str, body: SettingWrite,
    if_match: int, principal: Principal,
) -> SettingSlotRead:
    declaration = area_service.require_visible_setting(db, dealership_id=dealership_id, key=key)
    slot = _refusals(
        lambda: store.change_setting(
            db,
            dealership_id=dealership_id,
            location_id=location_id,
            key=key,
            value=_value_of_body(declaration, body.value),
            expected_version=if_match,
            actor_id=principal.user_id,
        )
    )
    db.commit()
    return _slot_read(slot)


@router.put("/dealerships/{dealership_id}/setting-values/{key}", response_model=SettingSlotRead)
def put_setting_value(
    dealership_id: uuid.UUID,
    key: str,
    body: SettingWrite,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("dealership_settings")),
    db: Session = Depends(get_db),
):
    """The Betrieb's own value of `key`."""

    _guard(db, dealership_id, principal)
    return _write(
        db, dealership_id=dealership_id, location_id=None, key=key, body=body, if_match=if_match, principal=principal
    )


@router.put(
    "/dealerships/{dealership_id}/setting-values/{key}/locations/{location_id}", response_model=SettingSlotRead
)
def put_location_setting_value(
    dealership_id: uuid.UUID,
    key: str,
    location_id: uuid.UUID,
    body: SettingWrite,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("dealership_settings")),
    db: Session = Depends(get_db),
):
    """A location's own value of `key`; `null` makes it take the Betrieb's again."""

    _guard(db, dealership_id, principal)
    return _write(
        db, dealership_id=dealership_id, location_id=location_id, key=key, body=body, if_match=if_match,
        principal=principal,
    )


@router.get("/dealerships/{dealership_id}/setting-values/{key}/history", response_model=SettingChangePage)
def get_setting_history(
    dealership_id: uuid.UUID,
    key: str,
    location_id: uuid.UUID | None = Query(default=None, alias="locationId"),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(require_read("dealership_settings")),
    db: Session = Depends(get_db),
):
    """Every change of the slot (the Betrieb's, or `locationId`'s), newest first."""

    _guard(db, dealership_id, principal)
    area_service.require_visible_setting(db, dealership_id=dealership_id, key=key)
    sort_fields = store.history_sort_fields()
    params = SortPageParams(limit=limit, cursor=decode_cursor_for_sort(cursor, sort_fields), sort_fields=sort_fields)
    changes, next_cursor = store.setting_history_page(
        db, dealership_id=dealership_id, location_id=location_id, key=key, params=params
    )
    return SettingChangePage(items=[_change_read(change) for change in changes], next_cursor=next_cursor)


@router.post("/dealerships/{dealership_id}/setting-changes/{change_id}/restore", response_model=SettingSlotRead)
def restore_setting_change(
    dealership_id: uuid.UUID,
    change_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("dealership_settings")),
    db: Session = Depends(get_db),
):
    """Brings back the value an earlier change wrote, as a change of its own
    (Anto, 2026-11-24). If-Match names the version of the slot it restores into."""

    _guard(db, dealership_id, principal)
    change = store.get_change(db, dealership_id=dealership_id, change_id=change_id)
    area_service.require_visible_setting(db, dealership_id=dealership_id, key=change.key)
    slot = _refusals(
        lambda: store.restore_change(
            db, dealership_id=dealership_id, change_id=change_id, expected_version=if_match,
            actor_id=principal.user_id,
        )
    )
    db.commit()
    return _slot_read(slot)
