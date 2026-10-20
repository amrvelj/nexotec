"""My account (KAN-201): the caller edits their own profile — names (Anto,
2026-10-20: the user may change their own name), mobile, direct dial,
correspondence language and signature block. Everything else on the user is
the manager's (the Users screen); the e-mail address is the sign-in and
recovery channel and is never the user's to change (KAN-196)."""

import uuid

from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.platform.models.user import User
from app.platform.schemas.me import MeRead, MeUpdate
from app.platform.services import dealership as dealership_service

# Recorded with their values: what documents and colleagues see of the user.
_AUDITED_WITH_VALUES = {"first_name", "last_name", "correspondence_language"}
# Recorded as changed, without values: contact details and free text.
_AUDITED_AS_CHANGED = {"phone", "direct_dial", "signature_block"}


def me_read(db: Session, user: User) -> MeRead:
    return MeRead(
        id=user.id,
        dealership_id=user.tenant_id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        phone=user.phone,
        direct_dial=user.direct_dial,
        correspondence_language=user.correspondence_language,
        default_correspondence_language=dealership_service.get_dealership_default_correspondence_language(
            db, user.tenant_id
        ),
        signature_block=user.signature_block,
        has_signature_image=user.signature_image_ref is not None,
        version=user.version,
    )


def _plain(value: object) -> object:
    return getattr(value, "value", value)


def update_own_profile(db: Session, *, user: User, data: MeUpdate, actor_id: uuid.UUID) -> User:
    """The caller's row, locked by the route (`get_own_user_for_update_or_404`)
    and checked against If-Match. One `profile_updated` audit row naming the
    user as actor; a request that changes nothing writes nothing and keeps
    the version."""

    changes = data.model_dump(exclude_unset=True)
    before: dict[str, object] = {}
    after: dict[str, object] = {}
    changed: list[str] = []
    for field, value in changes.items():
        if field == "signature_block" and isinstance(value, str):
            value = value.strip() or None
        if getattr(user, field) == value:
            continue
        if field in _AUDITED_WITH_VALUES:
            before[field] = _plain(getattr(user, field))
            after[field] = _plain(value)
        elif field in _AUDITED_AS_CHANGED:
            changed.append(field)
        setattr(user, field, value)
    if not before and not changed:
        return user
    user.updated_by = actor_id
    user.version += 1
    if changed:
        after["changed"] = sorted(changed)
    record_audit_event(
        db,
        entity_type="user",
        entity_id=user.id,
        tenant_id=user.tenant_id,
        action="profile_updated",
        actor_id=actor_id,
        before=before or None,
        after=after,
    )
    db.commit()
    db.refresh(user)
    return user
