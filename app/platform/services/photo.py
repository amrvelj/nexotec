"""A user's photo (KAN-401; Dealer Administration, Users → Fields: "photo
of the person"). The same shape as the signature image (`signature.py`,
KAN-200): stored in the object store under a tenant-partitioned key, the
row holds the key only; PNG or JPEG, judged by the bytes (the magic
number), not by the header the client sent. At most 5 MiB — a portrait
straight from a phone, not a print scan."""

import uuid

from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import NotFoundError, UnprocessableEntityError
from app.core.object_store import ObjectStore, StoredObject
from app.platform.models.user import User

MAX_BYTES = 5 * 1024 * 1024
_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"


def photo_key(user: User) -> str:
    return f"dealerships/{user.tenant_id}/users/{user.id}/photo"


def _detect_content_type(data: bytes) -> str:
    if data.startswith(_PNG):
        return "image/png"
    if data.startswith(_JPEG):
        return "image/jpeg"
    raise UnprocessableEntityError("The photo must be a PNG or JPEG file.", details={"reason": "photo_not_an_image"})


def set_photo(db: Session, store: ObjectStore, *, user: User, data: bytes, content_type: str, actor_id: uuid.UUID) -> None:
    if not data:
        raise UnprocessableEntityError("The request body must be the image.", details={"reason": "photo_not_an_image"})
    if len(data) > MAX_BYTES:
        raise UnprocessableEntityError(
            "The photo is too large — at most 5 MiB.",
            details={"reason": "photo_too_large", "maxBytes": MAX_BYTES, "bytes": len(data)},
        )
    detected = _detect_content_type(data)
    declared = content_type.split(";", 1)[0].strip().lower()
    if declared and declared != detected:
        raise UnprocessableEntityError(
            "The Content-Type does not match the image.",
            details={"reason": "photo_not_an_image", "declared": declared, "detected": detected},
        )
    key = photo_key(user)
    store.put(key, data, content_type=detected)
    had_one = user.photo_ref is not None
    user.photo_ref = key
    user.updated_by = actor_id
    user.version += 1
    record_audit_event(
        db,
        entity_type="user",
        entity_id=user.id,
        tenant_id=user.tenant_id,
        action="photo_set",
        actor_id=actor_id,
        before={"has_photo": had_one},
        after={"has_photo": True, "content_type": detected, "bytes": len(data)},
    )
    db.commit()
    db.refresh(user)


def get_photo(store: ObjectStore, *, user: User) -> StoredObject:
    if user.photo_ref is None:
        raise NotFoundError("This user has no photo.")
    stored = store.get(user.photo_ref)
    if stored is None:
        raise NotFoundError("This user has no photo.")
    return stored


def remove_photo(db: Session, store: ObjectStore, *, user: User, actor_id: uuid.UUID) -> None:
    if user.photo_ref is None:
        raise NotFoundError("This user has no photo.")
    store.delete(user.photo_ref)
    user.photo_ref = None
    user.updated_by = actor_id
    user.version += 1
    record_audit_event(
        db,
        entity_type="user",
        entity_id=user.id,
        tenant_id=user.tenant_id,
        action="photo_removed",
        actor_id=actor_id,
        before={"has_photo": True},
        after={"has_photo": False},
    )
    db.commit()
    db.refresh(user)
