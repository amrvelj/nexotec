"""A user's signature image (KAN-200; Dealer Administration, Users →
Personal settings: "signature block and signature image"). Stored in the
object store under a tenant-partitioned key; the row holds the key only.
PNG or JPEG, judged by the bytes (the magic number), not by the header the
client sent; at most 1 MiB — a signature, not a photo."""

import uuid

from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import NotFoundError, UnprocessableEntityError
from app.core.object_store import ObjectStore, StoredObject
from app.platform.models.user import User

MAX_BYTES = 1024 * 1024
_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"


def signature_key(user: User) -> str:
    return f"dealerships/{user.tenant_id}/users/{user.id}/signature"


def _detect_content_type(data: bytes) -> str:
    if data.startswith(_PNG):
        return "image/png"
    if data.startswith(_JPEG):
        return "image/jpeg"
    raise UnprocessableEntityError("The signature image must be a PNG or JPEG file.")


def set_signature(
    db: Session, store: ObjectStore, *, user: User, data: bytes, content_type: str, actor_id: uuid.UUID
) -> None:
    if not data:
        raise UnprocessableEntityError("The request body must be the image.")
    if len(data) > MAX_BYTES:
        raise UnprocessableEntityError(
            "The signature image is too large — at most 1 MiB.", details={"maxBytes": MAX_BYTES, "bytes": len(data)}
        )
    detected = _detect_content_type(data)
    declared = content_type.split(";", 1)[0].strip().lower()
    if declared and declared != detected:
        raise UnprocessableEntityError(
            "The Content-Type does not match the image.", details={"declared": declared, "detected": detected}
        )
    key = signature_key(user)
    store.put(key, data, content_type=detected)
    had_one = user.signature_image_ref is not None
    user.signature_image_ref = key
    user.updated_by = actor_id
    user.version += 1
    record_audit_event(
        db,
        entity_type="user",
        entity_id=user.id,
        tenant_id=user.tenant_id,
        action="signature_image_set",
        actor_id=actor_id,
        before={"has_signature_image": had_one},
        after={"has_signature_image": True, "content_type": detected, "bytes": len(data)},
    )
    db.commit()
    db.refresh(user)


def get_signature(store: ObjectStore, *, user: User) -> StoredObject:
    if user.signature_image_ref is None:
        raise NotFoundError("This user has no signature image.")
    stored = store.get(user.signature_image_ref)
    if stored is None:
        raise NotFoundError("This user has no signature image.")
    return stored


def remove_signature(db: Session, store: ObjectStore, *, user: User, actor_id: uuid.UUID) -> None:
    if user.signature_image_ref is None:
        raise NotFoundError("This user has no signature image.")
    store.delete(user.signature_image_ref)
    user.signature_image_ref = None
    user.updated_by = actor_id
    user.version += 1
    record_audit_event(
        db,
        entity_type="user",
        entity_id=user.id,
        tenant_id=user.tenant_id,
        action="signature_image_removed",
        actor_id=actor_id,
        before={"has_signature_image": True},
        after={"has_signature_image": False},
    )
    db.commit()
    db.refresh(user)
