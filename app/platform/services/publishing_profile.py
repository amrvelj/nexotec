"""The publishing profile (KAN-225; Dealer Administration PRD, Publishing
profile — FR-A-04, FR-A-05, FR-A-27, A-RULE-2, D-A-26).

Rules held here:
- **One aggregate, one version.** The Betrieb's profile row carries the
  version of the whole publishing screen: every write on it — the profile,
  a location's override, the AGB file, imagery, lead routing — takes it as
  If-Match and moves it, under its row lock. The row exists for no Betrieb
  until its first write; a read answers a transient empty profile with
  version 1 (the `dealership_settings` pattern: a read never writes).
- **Inherit unless set (FR-A-04).** A location's override row holds only
  what differs; `effective_profile` lays it over the Betrieb's, per field
  and, for a localised text, per language.
- **Never a second copy of a fact.** The trade name and the documents'
  website URL are read from the dealership (Business), the lead e-mail is
  `location.lead_email` (written here through the location row, its
  version moved and audited like a Locations save), the logo is Branding's.
- **Files are judged by their bytes and kept in the object store**, never in
  a row: imagery JPEG or PNG, at least 800 px on its long edge; the AGB a
  PDF. Keys are tenant-partitioned. No public URL: the SPA reads them
  through the authenticated GET; the hosted website's signed URLs come with
  the bucket (KAN-402).
- **Validation in the dealer's words (FR-A-05)** — `missing_items` returns
  stable codes the SPA renders in four languages; Stock calls it before a
  website publish, so a missing Impressum is never discovered at the channel.
  Which items the website channel needs is a labelled default
  (`WEBSITE_REQUIREMENTS`) until Anto rules on it.
- Every write is audited with before and after.
"""

import logging
import struct
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import BadRequestError, NotFoundError, UnprocessableEntityError
from app.core.object_store import ObjectStore, StoredObject
from app.core.uuid7 import uuid7
from app.platform.models.user import User, UserStatus
from app.platform.organisation.models.branding_asset import (
    IMAGERY_KINDS,
    BrandingAsset,
    BrandingAssetKind,
    PublishingImageKind,
)
from app.platform.organisation.models.dealership import Dealership, Location
from app.platform.organisation.models.publishing_profile import (
    BETRIEB_ONLY_LOCALISED_FIELDS,
    BETRIEB_ONLY_SCALAR_FIELDS,
    OVERRIDABLE_LOCALISED_FIELDS,
    OVERRIDABLE_SCALAR_FIELDS,
    LocationLeadRouting,
    PublishingProfile,
)
from app.platform.organisation.schemas.publishing_profile import (
    LeadRoutingUpdate,
    LocationOverrideUpdate,
    PublishingProfileUpdate,
)

logger = logging.getLogger("app.platform.services.publishing_profile")

_ENTITY = "publishing_profile"
_ALL_LOCALISED = OVERRIDABLE_LOCALISED_FIELDS + BETRIEB_ONLY_LOCALISED_FIELDS
_ALL_SCALAR = OVERRIDABLE_SCALAR_FIELDS + BETRIEB_ONLY_SCALAR_FIELDS

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MIN_IMAGE_LONG_EDGE_PX = 800
MAX_IMAGES_PER_KIND = 20
MAX_AGB_BYTES = 10 * 1024 * 1024
_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_PDF = b"%PDF-"


# --- The aggregate ---------------------------------------------------------------------------


def _new_profile(dealership_id: uuid.UUID, location_id: uuid.UUID | None, actor_id: uuid.UUID | None) -> PublishingProfile:
    # Set explicitly: the transient read row is never flushed, so the column
    # defaults would not apply to it.
    row = PublishingProfile(
        dealership_id=dealership_id,
        location_id=location_id,
        is_published=False,
        social_profiles={},
        created_by=actor_id,
        updated_by=actor_id,
    )
    for field in _ALL_LOCALISED:
        setattr(row, field, {})
    return row


def _betrieb_stmt(dealership_id: uuid.UUID):
    return select(PublishingProfile).where(
        PublishingProfile.dealership_id == dealership_id, PublishingProfile.location_id.is_(None)
    )


def read_profile(db: Session, dealership_id: uuid.UUID) -> PublishingProfile:
    """The Betrieb's profile to READ — stored, or a transient empty one at
    version 1 (the version its first write creates it with)."""

    row = db.scalar(_betrieb_stmt(dealership_id))
    if row is not None:
        return row
    transient = _new_profile(dealership_id, None, None)
    transient.version = 1
    transient.created_at = transient.updated_at = utcnow()
    return transient


def profile_for_update(db: Session, dealership_id: uuid.UUID) -> PublishingProfile:
    """The Betrieb's profile row, locked; created empty on the first write
    (two first writes at once: the loser's insert fails, it reads the
    winner's — the KAN-393 savepoint pattern)."""

    stmt = _betrieb_stmt(dealership_id).with_for_update().execution_options(populate_existing=True)
    row = db.scalar(stmt)
    if row is not None:
        return row
    db.flush()
    try:
        with db.begin_nested():
            db.add(_new_profile(dealership_id, None, None))
    except IntegrityError:
        pass
    row = db.scalar(stmt)
    assert row is not None, "publishing_profile row missing after its first INSERT or the concurrent winner's"
    return row


def _touch(profile: PublishingProfile, actor_id: uuid.UUID | None) -> None:
    profile.updated_by = actor_id
    profile.version += 1


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return dict(value)
    return value


def _snapshot(row: PublishingProfile, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: _jsonable(getattr(row, field)) for field in fields}


def _audit_diff(
    db: Session,
    *,
    profile: PublishingProfile,
    action: str,
    before: dict[str, Any],
    after: dict[str, Any],
    actor_id: uuid.UUID | None,
    extra: dict[str, Any] | None = None,
) -> None:
    diff_before = {k: v for k, v in before.items() if after.get(k) != v}
    diff_after = {k: v for k, v in after.items() if before.get(k) != v}
    if not (diff_before or diff_after) and extra is None:
        return
    record_audit_event(
        db,
        entity_type=_ENTITY,
        entity_id=profile.id,
        tenant_id=profile.dealership_id,
        action=action,
        actor_id=actor_id,
        before={**(extra or {}), **diff_before} or None,
        after={**(extra or {}), **diff_after} or None,
    )


def _apply(row: PublishingProfile, changes: dict[str, Any]) -> None:
    for field, value in changes.items():
        if field in _ALL_LOCALISED or field == "social_profiles":
            setattr(row, field, dict(value or {}))
        else:
            setattr(row, field, value)


def update_profile(
    db: Session, *, profile: PublishingProfile, data: PublishingProfileUpdate, actor_id: uuid.UUID
) -> PublishingProfile:
    changes = data.model_dump(exclude_unset=True)
    fields = _ALL_LOCALISED + _ALL_SCALAR
    before = _snapshot(profile, fields)
    _apply(profile, changes)
    _touch(profile, actor_id)
    db.flush()
    _audit_diff(db, profile=profile, action="update", before=before, after=_snapshot(profile, fields), actor_id=actor_id)
    db.commit()
    db.refresh(profile)
    return profile


# --- Location overrides (FR-A-04) -------------------------------------------------------------

_OVERRIDE_FIELDS = OVERRIDABLE_LOCALISED_FIELDS + OVERRIDABLE_SCALAR_FIELDS


def list_overrides(db: Session, dealership_id: uuid.UUID) -> list[PublishingProfile]:
    return list(
        db.scalars(
            select(PublishingProfile)
            .where(PublishingProfile.dealership_id == dealership_id, PublishingProfile.location_id.is_not(None))
            .order_by(PublishingProfile.created_at)
        ).all()
    )


def _override(db: Session, dealership_id: uuid.UUID, location_id: uuid.UUID) -> PublishingProfile | None:
    return db.scalar(
        select(PublishingProfile).where(
            PublishingProfile.dealership_id == dealership_id, PublishingProfile.location_id == location_id
        )
    )


def create_override(
    db: Session, *, profile: PublishingProfile, location: Location, data: LocationOverrideUpdate, actor_id: uuid.UUID
) -> PublishingProfile:
    """Switches a site's override on, with whatever it already differs in.
    A second switch-on is a no-op answer, not a refusal (an Idempotency-Key
    replay and a double click read the same)."""

    existing = _override(db, profile.dealership_id, location.id)
    if existing is not None:
        return existing
    row = _new_profile(profile.dealership_id, location.id, actor_id)
    _apply(row, data.model_dump(exclude_unset=True))
    db.add(row)
    _touch(profile, actor_id)
    db.flush()
    _audit_diff(
        db,
        profile=profile,
        action="location_override_created",
        before={},
        after=_snapshot(row, _OVERRIDE_FIELDS),
        actor_id=actor_id,
        extra={"location_id": str(location.id)},
    )
    db.commit()
    db.refresh(row)
    return row


def update_override(
    db: Session, *, profile: PublishingProfile, location: Location, data: LocationOverrideUpdate, actor_id: uuid.UUID
) -> PublishingProfile:
    row = _override(db, profile.dealership_id, location.id)
    if row is None:
        raise NotFoundError("This location has no override of its own — switch it on first.")
    before = _snapshot(row, _OVERRIDE_FIELDS)
    _apply(row, data.model_dump(exclude_unset=True))
    row.updated_by = actor_id
    row.version += 1
    _touch(profile, actor_id)
    db.flush()
    _audit_diff(
        db,
        profile=profile,
        action="location_override_updated",
        before=before,
        after=_snapshot(row, _OVERRIDE_FIELDS),
        actor_id=actor_id,
        extra={"location_id": str(location.id)},
    )
    db.commit()
    db.refresh(row)
    return row


def delete_override(db: Session, *, profile: PublishingProfile, location: Location, actor_id: uuid.UUID) -> None:
    """Switches a site's override off: it inherits everything again."""

    row = _override(db, profile.dealership_id, location.id)
    if row is None:
        raise NotFoundError("This location has no override of its own.")
    before = _snapshot(row, _OVERRIDE_FIELDS)
    db.delete(row)
    _touch(profile, actor_id)
    db.flush()
    _audit_diff(
        db,
        profile=profile,
        action="location_override_removed",
        before=before,
        after={},
        actor_id=actor_id,
        extra={"location_id": str(location.id)},
    )
    db.commit()


@dataclass(frozen=True)
class EffectiveProfile:
    dealership_id: uuid.UUID
    location_id: uuid.UUID | None
    trade_name: str | None
    values: dict[str, Any]
    has_agb_document: bool


def _merge_localised(base: dict[str, str], override: dict[str, str] | None) -> dict[str, str]:
    merged = dict(base or {})
    merged.update(override or {})
    return merged


def effective_profile(db: Session, dealership: Dealership, location_id: uuid.UUID | None) -> EffectiveProfile:
    """The profile a channel publishes for a site: its override laid over
    the Betrieb's, per field and per language (FR-A-04)."""

    base = read_profile(db, dealership.id)
    override = _override(db, dealership.id, location_id) if location_id is not None else None
    values: dict[str, Any] = {}
    for field in OVERRIDABLE_LOCALISED_FIELDS:
        values[field] = _merge_localised(getattr(base, field), getattr(override, field) if override else None)
    for field in OVERRIDABLE_SCALAR_FIELDS:
        own = getattr(override, field) if override is not None else None
        values[field] = own if own is not None else getattr(base, field)
    for field in BETRIEB_ONLY_LOCALISED_FIELDS:
        values[field] = dict(getattr(base, field) or {})
    for field in BETRIEB_ONLY_SCALAR_FIELDS:
        values[field] = getattr(base, field)
    values["social_profiles"] = dict(base.social_profiles or {})
    return EffectiveProfile(
        dealership_id=dealership.id,
        location_id=location_id,
        trade_name=dealership.trade_name or dealership.legal_name,
        values=values,
        has_agb_document=base.agb_object_key is not None,
    )


# --- AGB file --------------------------------------------------------------------------------


def agb_key(dealership_id: uuid.UUID, object_id: uuid.UUID) -> str:
    return f"dealerships/{dealership_id}/publishing/agb/{object_id}"


def _remove_object(store: ObjectStore, key: str) -> None:
    try:
        store.delete(key)
    except Exception:  # the row no longer names it; an orphan file is not a wrong record
        logger.exception("publishing profile: could not remove the replaced file %s", key)


def set_agb(db: Session, store: ObjectStore, *, profile: PublishingProfile, data: bytes, actor_id: uuid.UUID) -> PublishingProfile:
    if not data.startswith(_PDF):
        raise UnprocessableEntityError("The AGB must be a PDF file.", details={"reason": "agb_not_a_pdf"})
    if len(data) > MAX_AGB_BYTES:
        raise UnprocessableEntityError(
            "The AGB file is too large — at most 10 MiB.",
            details={"reason": "agb_too_large", "maxBytes": MAX_AGB_BYTES, "bytes": len(data)},
        )
    previous_key = profile.agb_object_key
    key = agb_key(profile.dealership_id, uuid7())
    store.put(key, data, content_type="application/pdf")
    try:
        profile.agb_object_key = key
        profile.agb_byte_size = len(data)
        profile.agb_uploaded_at = utcnow()
        _touch(profile, actor_id)
        db.flush()
        record_audit_event(
            db,
            entity_type=_ENTITY,
            entity_id=profile.id,
            tenant_id=profile.dealership_id,
            action="agb_document_set",
            actor_id=actor_id,
            before={"agb_document": previous_key is not None},
            after={"agb_document": True, "byte_size": len(data)},
        )
        db.commit()
    except BaseException:
        db.rollback()
        _remove_object(store, key)
        raise
    if previous_key is not None:
        _remove_object(store, previous_key)
    db.refresh(profile)
    return profile


def remove_agb(db: Session, store: ObjectStore, *, profile: PublishingProfile, actor_id: uuid.UUID) -> PublishingProfile:
    key = profile.agb_object_key
    if key is None:
        raise NotFoundError("This business has no AGB file.")
    profile.agb_object_key = None
    profile.agb_byte_size = None
    profile.agb_uploaded_at = None
    _touch(profile, actor_id)
    db.flush()
    record_audit_event(
        db,
        entity_type=_ENTITY,
        entity_id=profile.id,
        tenant_id=profile.dealership_id,
        action="agb_document_removed",
        actor_id=actor_id,
        before={"agb_document": True},
        after={"agb_document": False},
    )
    db.commit()
    _remove_object(store, key)
    db.refresh(profile)
    return profile


def read_agb(db: Session, store: ObjectStore, *, dealership_id: uuid.UUID) -> StoredObject:
    profile = db.scalar(_betrieb_stmt(dealership_id))
    if profile is None or profile.agb_object_key is None:
        raise NotFoundError("This business has no AGB file.")
    stored = store.get(profile.agb_object_key)
    if stored is None:
        logger.error("publishing profile: %s names %s, which the store does not hold", profile.id, profile.agb_object_key)
        raise NotFoundError("This business has no AGB file.")
    return stored


# --- Imagery (premises · team · hero, per location) ------------------------------------------


@dataclass(frozen=True)
class ValidatedImage:
    data: bytes
    mime_type: str
    width: int
    height: int


def _refuse_image(message: str, reason: str, **details: object) -> UnprocessableEntityError:
    return UnprocessableEntityError(message, details={"reason": reason, **details})


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise _refuse_image("The PNG file is damaged.", "image_unreadable")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg_size(data: bytes) -> tuple[int, int]:
    """Width and height from the first start-of-frame segment."""

    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            i += 1 if marker == 0xFF else 2
            continue
        if marker == 0xD9:
            break
        (length,) = struct.unpack(">H", data[i + 2 : i + 4])
        if marker in _SOF_MARKERS:
            if i + 9 > len(data):
                break
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        i += 2 + length
    raise _refuse_image("The JPEG file is damaged.", "image_unreadable")


def validate_image(data: bytes) -> ValidatedImage:
    if not data:
        raise _refuse_image("The request body must be the image.", "image_not_an_image")
    if len(data) > MAX_IMAGE_BYTES:
        raise _refuse_image(
            "The image is too large — at most 10 MiB.", "image_too_large", maxBytes=MAX_IMAGE_BYTES, bytes=len(data)
        )
    if data.startswith(_PNG):
        mime, (width, height) = "image/png", _png_size(data)
    elif data.startswith(_JPEG):
        mime, (width, height) = "image/jpeg", _jpeg_size(data)
    else:
        raise _refuse_image("The image must be a JPEG or PNG file.", "image_not_an_image")
    if max(width, height) < MIN_IMAGE_LONG_EDGE_PX:
        raise _refuse_image(
            f"The image is too small — at least {MIN_IMAGE_LONG_EDGE_PX} px on its long edge.",
            "image_too_small",
            minLongEdgePx=MIN_IMAGE_LONG_EDGE_PX,
            width=width,
            height=height,
        )
    return ValidatedImage(data=data, mime_type=mime, width=width, height=height)


def image_key(dealership_id: uuid.UUID, location_id: uuid.UUID, kind: BrandingAssetKind, asset_id: uuid.UUID) -> str:
    return f"dealerships/{dealership_id}/publishing/locations/{location_id}/{kind.value}/{asset_id}"


def list_images(db: Session, dealership_id: uuid.UUID) -> list[BrandingAsset]:
    return list(
        db.scalars(
            select(BrandingAsset)
            .where(BrandingAsset.dealership_id == dealership_id, BrandingAsset.kind.in_(IMAGERY_KINDS))
            .order_by(BrandingAsset.location_id, BrandingAsset.kind, BrandingAsset.position)
        ).all()
    )


def _images_of(db: Session, dealership_id: uuid.UUID, location_id: uuid.UUID, kind: BrandingAssetKind) -> list[BrandingAsset]:
    return list(
        db.scalars(
            select(BrandingAsset)
            .where(
                BrandingAsset.dealership_id == dealership_id,
                BrandingAsset.location_id == location_id,
                BrandingAsset.kind == kind,
            )
            .order_by(BrandingAsset.position)
        ).all()
    )


def _image_or_404(db: Session, dealership_id: uuid.UUID, image_id: uuid.UUID) -> BrandingAsset:
    image = db.scalar(
        select(BrandingAsset).where(
            BrandingAsset.id == image_id,
            BrandingAsset.dealership_id == dealership_id,
            BrandingAsset.kind.in_(IMAGERY_KINDS),
        )
    )
    if image is None:
        raise NotFoundError("Image not found.")
    return image


def _image_snapshot(image: BrandingAsset) -> dict[str, Any]:
    return {
        "image_id": str(image.id),
        "location_id": str(image.location_id),
        "kind": image.kind.value,
        "position": image.position,
        "is_primary": image.is_primary,
        "byte_size": image.byte_size,
    }


def add_image(
    db: Session,
    store: ObjectStore,
    *,
    profile: PublishingProfile,
    location: Location,
    kind: PublishingImageKind,
    data: bytes,
    actor_id: uuid.UUID,
) -> BrandingAsset:
    """Appends one image at the end of the site's list for `kind`; the
    first image of a kind becomes its primary."""

    stored_kind = BrandingAssetKind(kind.value)
    image = validate_image(data)
    existing = _images_of(db, profile.dealership_id, location.id, stored_kind)
    if len(existing) >= MAX_IMAGES_PER_KIND:
        raise UnprocessableEntityError(
            f"A location holds at most {MAX_IMAGES_PER_KIND} images of one kind.",
            details={"reason": "too_many_images", "max": MAX_IMAGES_PER_KIND},
        )
    asset_id = uuid7()
    key = image_key(profile.dealership_id, location.id, stored_kind, asset_id)
    store.put(key, image.data, content_type=image.mime_type)
    try:
        row = BrandingAsset(
            id=asset_id,
            dealership_id=profile.dealership_id,
            location_id=location.id,
            kind=stored_kind,
            object_key=key,
            mime_type=image.mime_type,
            width=image.width,
            height=image.height,
            byte_size=len(image.data),
            uploaded_by=actor_id,
            position=(max(i.position or 0 for i in existing) + 1) if existing else 0,
            is_primary=not existing,
        )
        db.add(row)
        _touch(profile, actor_id)
        db.flush()
        record_audit_event(
            db,
            entity_type=_ENTITY,
            entity_id=profile.id,
            tenant_id=profile.dealership_id,
            action="image_added",
            actor_id=actor_id,
            after=_image_snapshot(row),
        )
        db.commit()
    except BaseException:
        db.rollback()
        _remove_object(store, key)
        raise
    db.refresh(row)
    return row


def reorder_images(
    db: Session,
    *,
    profile: PublishingProfile,
    location: Location,
    kind: PublishingImageKind,
    ordered_ids: list[uuid.UUID],
    actor_id: uuid.UUID,
) -> list[BrandingAsset]:
    images = _images_of(db, profile.dealership_id, location.id, BrandingAssetKind(kind.value))
    by_id = {i.id: i for i in images}
    if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != set(by_id):
        raise BadRequestError("Name every image of this location and kind exactly once.")
    before = [str(i.id) for i in images]
    for position, image_id in enumerate(ordered_ids):
        by_id[image_id].position = position
    _touch(profile, actor_id)
    db.flush()
    record_audit_event(
        db,
        entity_type=_ENTITY,
        entity_id=profile.id,
        tenant_id=profile.dealership_id,
        action="images_reordered",
        actor_id=actor_id,
        before={"location_id": str(location.id), "kind": kind.value, "order": before},
        after={"location_id": str(location.id), "kind": kind.value, "order": [str(i) for i in ordered_ids]},
    )
    db.commit()
    return _images_of(db, profile.dealership_id, location.id, BrandingAssetKind(kind.value))


def set_primary_image(db: Session, *, profile: PublishingProfile, image_id: uuid.UUID, actor_id: uuid.UUID) -> BrandingAsset:
    image = _image_or_404(db, profile.dealership_id, image_id)
    assert image.location_id is not None
    siblings = _images_of(db, profile.dealership_id, image.location_id, image.kind)
    previous = next((i for i in siblings if i.is_primary), None)
    if previous is not None and previous.id != image.id:
        previous.is_primary = False
        db.flush()  # one primary per location and kind at any moment
    image.is_primary = True
    _touch(profile, actor_id)
    db.flush()
    record_audit_event(
        db,
        entity_type=_ENTITY,
        entity_id=profile.id,
        tenant_id=profile.dealership_id,
        action="image_primary_set",
        actor_id=actor_id,
        before={"primary": str(previous.id) if previous else None},
        after={"primary": str(image.id)},
    )
    db.commit()
    db.refresh(image)
    return image


def remove_image(db: Session, store: ObjectStore, *, profile: PublishingProfile, image_id: uuid.UUID, actor_id: uuid.UUID) -> None:
    """Removes one image; when it was the primary, the next in order takes
    over, so a kind with images always has one."""

    image = _image_or_404(db, profile.dealership_id, image_id)
    assert image.location_id is not None
    key = image.object_key
    snapshot = _image_snapshot(image)
    was_primary = image.is_primary
    location_id, kind = image.location_id, image.kind
    db.delete(image)
    db.flush()
    remaining = _images_of(db, profile.dealership_id, location_id, kind)
    for position, other in enumerate(remaining):
        other.position = position
    if was_primary and remaining:
        remaining[0].is_primary = True
    _touch(profile, actor_id)
    db.flush()
    record_audit_event(
        db,
        entity_type=_ENTITY,
        entity_id=profile.id,
        tenant_id=profile.dealership_id,
        action="image_removed",
        actor_id=actor_id,
        before=snapshot,
    )
    db.commit()
    _remove_object(store, key)


def read_image(db: Session, store: ObjectStore, *, dealership_id: uuid.UUID, image_id: uuid.UUID) -> StoredObject:
    image = _image_or_404(db, dealership_id, image_id)
    stored = store.get(image.object_key)
    if stored is None:
        logger.error("publishing profile: image %s names %s, which the store does not hold", image.id, image.object_key)
        raise NotFoundError("Image not found.")
    return stored


# --- Lead routing (per location) -------------------------------------------------------------


def list_lead_routing(db: Session, dealership_id: uuid.UUID) -> list[tuple[Location, LocationLeadRouting | None]]:
    locations = db.scalars(
        select(Location).where(Location.tenant_id == dealership_id).order_by(Location.is_active.desc(), Location.name)
    ).all()
    routing = {
        r.location_id: r
        for r in db.scalars(select(LocationLeadRouting).where(LocationLeadRouting.dealership_id == dealership_id)).all()
    }
    return [(location, routing.get(location.id)) for location in locations]


def _routing_snapshot(location: Location, routing: LocationLeadRouting | None) -> dict[str, Any]:
    return {
        "lead_email": location.lead_email,
        "lead_phone": routing.lead_phone if routing else None,
        "creates_task": routing.creates_task if routing else False,
        "recipient_user_id": str(routing.recipient_user_id) if routing and routing.recipient_user_id else None,
        "recipient_role": routing.recipient_role if routing else None,
    }


def set_lead_routing(
    db: Session, *, profile: PublishingProfile, location: Location, data: LeadRoutingUpdate, actor_id: uuid.UUID
) -> tuple[Location, LocationLeadRouting]:
    """Replaces a site's lead routing. The caller holds the location's row
    lock: its `lead_email` is the Locations master data's own column, so a
    change moves the location's version and is audited as its update."""

    if data.recipient_user_id is not None:
        user = db.scalar(
            select(User).where(
                User.id == data.recipient_user_id,
                User.tenant_id == profile.dealership_id,
                User.status != UserStatus.DEACTIVATED,
            )
        )
        if user is None:
            raise UnprocessableEntityError(
                "The recipient must be a user of this business who is not deactivated.",
                details={"reason": "lead_recipient_not_a_user"},
            )
    routing = db.scalar(select(LocationLeadRouting).where(LocationLeadRouting.location_id == location.id))
    before = _routing_snapshot(location, routing)
    if routing is None:
        routing = LocationLeadRouting(location_id=location.id, dealership_id=profile.dealership_id, created_by=actor_id)
        db.add(routing)
    else:
        routing.version += 1
    routing.lead_phone = data.lead_phone
    routing.creates_task = data.creates_task
    routing.recipient_user_id = data.recipient_user_id
    routing.recipient_role = data.recipient_role.value if data.recipient_role is not None else None
    routing.updated_by = actor_id
    if location.lead_email != data.lead_email:
        old = location.lead_email
        location.lead_email = data.lead_email
        location.updated_by = actor_id
        location.version += 1
        record_audit_event(
            db,
            entity_type="location",
            entity_id=location.id,
            tenant_id=location.tenant_id,
            action="update",
            actor_id=actor_id,
            before={"lead_email": old},
            after={"lead_email": data.lead_email},
        )
    _touch(profile, actor_id)
    db.flush()
    _audit_diff(
        db,
        profile=profile,
        action="lead_routing_set",
        before=before,
        after=_routing_snapshot(location, routing),
        actor_id=actor_id,
        extra={"location_id": str(location.id)},
    )
    db.commit()
    db.refresh(routing)
    db.refresh(location)
    return location, routing


# --- Validation for a channel (FR-A-05) -------------------------------------------------------


@dataclass(frozen=True)
class MissingItem:
    code: str
    section: str
    location_id: uuid.UUID | None = None
    location_name: str | None = None


# The website channel's needs — a labelled default (KAN-225, put to Anto on
# the ticket): the legal minimum a Swiss commercial website carries
# (Impressum, privacy notice), the hosted site's domain, a description in
# the Betrieb's correspondence language, and somewhere for every active
# site's enquiries to go.
WEBSITE_REQUIREMENTS = (
    "website_domain_missing",
    "short_description_missing",
    "impressum_missing",
    "privacy_notice_missing",
    "lead_email_missing",
)


def missing_items(db: Session, dealership: Dealership, channel: str) -> list[MissingItem]:
    if channel != "website":
        raise BadRequestError(f"'{channel}' is not a channel the profile is validated for.")
    profile = read_profile(db, dealership.id)
    language = dealership.default_correspondence_language.value
    missing: list[MissingItem] = []
    if not profile.website_domain:
        missing.append(MissingItem("website_domain_missing", "identity"))
    if not (profile.short_description or {}).get(language):
        missing.append(MissingItem("short_description_missing", "identity"))
    if not (profile.impressum or {}).get(language):
        missing.append(MissingItem("impressum_missing", "legal"))
    if not (profile.privacy_notice or {}).get(language):
        missing.append(MissingItem("privacy_notice_missing", "legal"))
    for location in db.scalars(
        select(Location).where(Location.tenant_id == dealership.id, Location.is_active.is_(True)).order_by(Location.name)
    ).all():
        if not location.lead_email:
            missing.append(MissingItem("lead_email_missing", "leadRouting", location.id, location.name))
    return missing
