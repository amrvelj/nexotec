"""Per-user UI preference persistence (UI/UX Core Principles § User-Level
Preference Persistence, blocker U-01). Self-scoped only — there is no
route parameter for user_id, the caller can only ever read/write their own
preferences, resolved from the JWT `sub` claim like every other `/v1/me/*`
concern. No access-role gate beyond being authenticated: reading or writing
your own layout state isn't a privileged operation.
"""

from fastapi import APIRouter, Depends, Path, Response
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.db import get_db
from app.platform.schemas.user_preference import (
    SCOPE_PATTERN,
    UserPreferenceListRead,
    UserPreferenceRead,
    UserPreferenceWrite,
)
from app.platform.services import user_preference as user_preference_service

router = APIRouter(tags=["user-preferences"])

ScopePath = Path(pattern=SCOPE_PATTERN)


@router.get("/me/preferences", response_model=UserPreferenceListRead)
def list_my_preferences(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    rows = user_preference_service.list_preferences(db, user_id=principal.user_id)
    return UserPreferenceListRead(
        items=[UserPreferenceRead.model_validate(row, from_attributes=True) for row in rows]
    )


@router.get("/me/preferences/{scope}", response_model=UserPreferenceRead)
def get_my_preference(
    scope: str = ScopePath,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    row = user_preference_service.get_preference(db, user_id=principal.user_id, scope=scope)
    if row is None:
        # Absent, not 404: an unset scope simply has no override yet — the
        # frontend falls back to the module default, same as after DELETE.
        return UserPreferenceRead(scope=scope, payload={}, updated_at=None)
    return UserPreferenceRead.model_validate(row, from_attributes=True)


@router.put("/me/preferences/{scope}", response_model=UserPreferenceRead)
def put_my_preference(
    body: UserPreferenceWrite,
    scope: str = ScopePath,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(mode="json", by_alias=True)
    row = user_preference_service.put_preference(
        db, user_id=principal.user_id, scope=scope, payload=payload, schema_version=body.schema_version
    )
    return UserPreferenceRead.model_validate(row, from_attributes=True)


@router.delete("/me/preferences/{scope}", status_code=204)
def delete_my_preference(
    scope: str = ScopePath,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Response:
    user_preference_service.delete_preference(db, user_id=principal.user_id, scope=scope)
    return Response(status_code=204)
