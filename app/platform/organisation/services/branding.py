"""The Betrieb's branding (KAN-205; Dealer Administration PRD, Logo and brand
assets): the brand colours with their contrast check, where the logo
appears, and the logo files.

Rules held here:
- **Logo files are judged by their bytes** (`logo_validation.py`): SVG
  (sanitised) or PNG ≥ 600 px on the long edge; JPEG refused with the reason.
- **Stored in the object store, never in a row**: the key is
  `dealerships/<id>/branding/<kind>/<asset id>`, tenant-partitioned, and a
  replaced file gets a new key — the row and the object it names change in
  one step, and the old object is removed only after the commit. No public
  URL exists: the renderer reads the bytes from the store in-process, the
  SPA through the authenticated `GET …/branding/assets/{kind}` (private,
  no-store), as the user photo and signature are served.
- **Variants fall back to `primary`** (`resolve_asset`): only the primary
  is mandatory.
- **The contrast check runs on every save** and its verdict is returned and
  audited with the save. A failing pair **warns, it does not block** — the
  PRD asks for the check but does not say it refuses the save; KAN-205's
  hand-over puts that question to Anto (warn / block / confirm-to-save).
- Every write moves the dealership's version (the branding is part of the
  Betrieb's record, and every write takes it as If-Match) and writes one
  audit row with before and after.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import NotFoundError
from app.core.i18n import SwissLanguage
from app.core.object_store import ObjectStore, StoredObject, get_object_store
from app.core.uuid7 import uuid7
from app.platform.organisation.models.branding_asset import BrandingAsset, BrandingAssetKind
from app.platform.organisation.models.dealership import Dealership, Location
from app.platform.organisation.schemas.branding import (
    BrandingAssetRead,
    BrandingPlacements,
    BrandingRead,
    BrandingUpdate,
    ContrastCheckRead,
    ContrastPairRead,
    LocationLogoRead,
)
from app.platform.organisation.services.brand_colors import HEX_COLOR, judge_brand_colors
from app.platform.organisation.services.logo_validation import validate_logo

logger = logging.getLogger("app.platform.organisation.services.branding")

_ENTITY = "dealership"
DEFAULT_BRAND_PRIMARY_COLOR = "#7C3AED"  # the column default (purple[6])
_PLACEMENT_COLUMNS = {
    "document_header": "logo_on_document_header",
    "price_tag": "logo_on_price_tag",
    "listing": "logo_on_listing",
    "email_signature": "logo_on_email_signature",
    "app_header": "logo_on_app_header",
}


def asset_key(
    dealership_id: uuid.UUID, kind: BrandingAssetKind, asset_id: uuid.UUID, *, location_id: uuid.UUID | None = None
) -> str:
    if location_id is not None:
        return f"dealerships/{dealership_id}/branding/locations/{location_id}/{kind.value}/{asset_id}"
    return f"dealerships/{dealership_id}/branding/{kind.value}/{asset_id}"


def list_assets(db: Session, dealership_id: uuid.UUID) -> dict[BrandingAssetKind, BrandingAsset]:
    """The Betrieb's own variants — never a location's letterhead logo."""

    rows = db.scalars(
        select(BrandingAsset).where(BrandingAsset.dealership_id == dealership_id, BrandingAsset.location_id.is_(None))
    ).all()
    return {row.kind: row for row in rows}


def list_location_logos(db: Session, dealership_id: uuid.UUID) -> dict[uuid.UUID, BrandingAsset]:
    """KAN-206: each site's own letterhead logo, by location id."""

    rows = db.scalars(
        select(BrandingAsset).where(
            BrandingAsset.dealership_id == dealership_id,
            BrandingAsset.location_id.is_not(None),
            BrandingAsset.kind == BrandingAssetKind.PRIMARY,
        )
    ).all()
    return {row.location_id: row for row in rows if row.location_id is not None}


def resolve_asset(db: Session, dealership_id: uuid.UUID, kind: BrandingAssetKind) -> BrandingAsset | None:
    """The file a reader of `kind` gets: its own, else the primary's."""

    assets = list_assets(db, dealership_id)
    return assets.get(kind) or assets.get(BrandingAssetKind.PRIMARY)


def _contrast(dealership: Dealership) -> ContrastCheckRead:
    # The API only ever writes #RRGGBB; a row written otherwise (by hand)
    # is judged as the renderer prints it — in the shipped default.
    primary = dealership.brand_primary_color
    if not HEX_COLOR.match(primary or ""):
        primary = DEFAULT_BRAND_PRIMARY_COLOR
    secondary = dealership.brand_secondary_color
    if secondary is not None and not HEX_COLOR.match(secondary):
        secondary = None
    pairs = judge_brand_colors(primary, secondary)
    return ContrastCheckRead(
        passes=all(p.passes for p in pairs),
        pairs=[
            ContrastPairRead(pair=p.pair, foreground=p.foreground, background=p.background, ratio=p.ratio, passes=p.passes)
            for p in pairs
        ],
    )


def _asset_read(kind: BrandingAssetKind, assets: dict[BrandingAssetKind, BrandingAsset]) -> BrandingAssetRead:
    own = assets.get(kind)
    if own is not None:
        return BrandingAssetRead(
            kind=kind,
            source="own",
            mime_type=own.mime_type,
            width=own.width,
            height=own.height,
            byte_size=own.byte_size,
            uploaded_by=own.uploaded_by,
            uploaded_at=own.uploaded_at,
        )
    if BrandingAssetKind.PRIMARY in assets:
        return BrandingAssetRead(kind=kind, source="primary")
    return BrandingAssetRead(kind=kind, source="none")


def branding_read(db: Session, dealership: Dealership) -> BrandingRead:
    assets = list_assets(db, dealership.id)
    return BrandingRead(
        dealership_id=dealership.id,
        primary_color=dealership.brand_primary_color,
        secondary_color=dealership.brand_secondary_color,
        placements=BrandingPlacements(
            **{name: getattr(dealership, column) for name, column in _PLACEMENT_COLUMNS.items()}
        ),
        assets=[_asset_read(kind, assets) for kind in BrandingAssetKind],
        location_logos=[
            LocationLogoRead(
                location_id=location_id,
                mime_type=row.mime_type,
                width=row.width,
                height=row.height,
                byte_size=row.byte_size,
                uploaded_by=row.uploaded_by,
                uploaded_at=row.uploaded_at,
            )
            for location_id, row in sorted(list_location_logos(db, dealership.id).items(), key=lambda item: str(item[0]))
        ],
        contrast=_contrast(dealership),
        version=dealership.version,
    )


def _settings_snapshot(dealership: Dealership) -> dict[str, Any]:
    return {
        "brand_primary_color": dealership.brand_primary_color,
        "brand_secondary_color": dealership.brand_secondary_color,
        **{column: getattr(dealership, column) for column in _PLACEMENT_COLUMNS.values()},
    }


def update_branding(db: Session, *, dealership: Dealership, data: BrandingUpdate, actor_id: uuid.UUID) -> Dealership:
    """Colours and placements. The caller holds the dealership row lock and
    has checked If-Match."""

    before = _settings_snapshot(dealership)
    sent = data.model_fields_set
    if "primary_color" in sent and data.primary_color is not None:
        dealership.brand_primary_color = data.primary_color
    if "secondary_color" in sent:
        dealership.brand_secondary_color = data.secondary_color
    if data.placements is not None:
        for name in data.placements.model_fields_set:
            setattr(dealership, _PLACEMENT_COLUMNS[name], getattr(data.placements, name))
    after = _settings_snapshot(dealership)
    if after == before:
        return dealership
    contrast = _contrast(dealership)
    dealership.updated_by = actor_id
    dealership.version += 1
    record_audit_event(
        db,
        entity_type=_ENTITY,
        entity_id=dealership.id,
        tenant_id=dealership.id,
        action="branding_updated",
        actor_id=actor_id,
        before={k: v for k, v in before.items() if v != after[k]},
        after={
            **{k: v for k, v in after.items() if before[k] != v},
            # The verdict the manager saved with — a failing pair is a
            # warning, so the record of having been warned is kept.
            "contrast": {p.pair: {"ratio": p.ratio, "passes": p.passes} for p in contrast.pairs},
        },
    )
    db.commit()
    db.refresh(dealership)
    return dealership


def _asset_snapshot(asset: BrandingAsset | None) -> dict[str, Any] | None:
    if asset is None:
        return None
    return {
        "asset_id": str(asset.id),
        "mime_type": asset.mime_type,
        "width": asset.width,
        "height": asset.height,
        "byte_size": asset.byte_size,
    }


def _current_asset(
    db: Session, dealership_id: uuid.UUID, kind: BrandingAssetKind, location_id: uuid.UUID | None
) -> BrandingAsset | None:
    if location_id is None:
        return list_assets(db, dealership_id).get(kind)
    return db.scalar(
        select(BrandingAsset).where(
            BrandingAsset.dealership_id == dealership_id,
            BrandingAsset.location_id == location_id,
            BrandingAsset.kind == kind,
        )
    )


def _where(location_id: uuid.UUID | None) -> dict[str, Any]:
    """The audit row names the site whose letterhead logo changed."""

    return {"location_id": str(location_id)} if location_id is not None else {}


def _remove_object_after_commit(store: ObjectStore, key: str) -> None:
    try:
        store.delete(key)
    except Exception:  # the row no longer names it; an orphan file is not a wrong record
        logger.exception("branding: could not remove the replaced logo file %s", key)


def set_asset(
    db: Session,
    store: ObjectStore,
    *,
    dealership: Dealership,
    kind: BrandingAssetKind,
    data: bytes,
    actor_id: uuid.UUID | None,
    reason: str | None = None,
    location: Location | None = None,
) -> Dealership:
    """Validates and stores one logo variant. The caller holds the
    dealership row lock and has checked If-Match. `actor_id` is None only for
    scripts/migrate_logo_urls_to_branding_assets.py (no user made it), which
    also names its `reason`; whatever else the caller changed on the session
    (that script clears `logo_url`) commits with the asset. With `location`
    (KAN-206), the file is that site's own letterhead logo — the caller has
    resolved the location inside this Betrieb."""

    logo = validate_logo(data)
    location_id = location.id if location is not None else None
    previous = _current_asset(db, dealership.id, kind, location_id)
    asset_id = uuid7()
    key = asset_key(dealership.id, kind, asset_id, location_id=location_id)
    store.put(key, logo.data, content_type=logo.mime_type)
    try:
        if previous is not None:
            db.delete(previous)
            db.flush()  # the unique (dealership, kind) sees one row at a time
        asset = BrandingAsset(
            id=asset_id,
            dealership_id=dealership.id,
            location_id=location_id,
            kind=kind,
            object_key=key,
            mime_type=logo.mime_type,
            width=logo.width,
            height=logo.height,
            byte_size=len(logo.data),
            uploaded_by=actor_id,
        )
        db.add(asset)
        dealership.updated_by = actor_id
        dealership.version += 1
        record_audit_event(
            db,
            entity_type=_ENTITY,
            entity_id=dealership.id,
            tenant_id=dealership.id,
            action="branding_asset_set",
            actor_id=actor_id,
            before={"kind": kind.value, **_where(location_id), "asset": _asset_snapshot(previous)},
            after={"kind": kind.value, **_where(location_id), "asset": _asset_snapshot(asset)},
            reason=reason,
        )
        db.commit()
    except BaseException:
        db.rollback()
        _remove_object_after_commit(store, key)  # nothing names the new file
        raise
    if previous is not None:
        _remove_object_after_commit(store, previous.object_key)
    db.refresh(dealership)
    return dealership


def remove_asset(
    db: Session,
    store: ObjectStore,
    *,
    dealership: Dealership,
    kind: BrandingAssetKind,
    actor_id: uuid.UUID,
    location: Location | None = None,
) -> Dealership:
    location_id = location.id if location is not None else None
    previous = _current_asset(db, dealership.id, kind, location_id)
    if previous is None:
        if location_id is not None:
            raise NotFoundError("This location has no letterhead logo of its own.")
        raise NotFoundError(f"This business has no {kind.value} logo of its own.")
    key = previous.object_key
    db.delete(previous)
    dealership.updated_by = actor_id
    dealership.version += 1
    record_audit_event(
        db,
        entity_type=_ENTITY,
        entity_id=dealership.id,
        tenant_id=dealership.id,
        action="branding_asset_removed",
        actor_id=actor_id,
        before={"kind": kind.value, **_where(location_id), "asset": _asset_snapshot(previous)},
        after={"kind": kind.value, **_where(location_id), "asset": None},
    )
    db.commit()
    _remove_object_after_commit(store, key)
    db.refresh(dealership)
    return dealership


def read_asset(db: Session, store: ObjectStore, *, dealership_id: uuid.UUID, kind: BrandingAssetKind) -> StoredObject:
    asset = resolve_asset(db, dealership_id, kind)
    if asset is None:
        raise NotFoundError("This business has no logo.")
    stored = store.get(asset.object_key)
    if stored is None:
        logger.error("branding: %s names %s, which the object store does not hold", asset.id, asset.object_key)
        raise NotFoundError("This business has no logo.")
    return stored


def read_location_logo(
    db: Session, store: ObjectStore, *, dealership_id: uuid.UUID, location_id: uuid.UUID
) -> StoredObject:
    """KAN-206: a site's own letterhead logo; 404 when it has none."""

    asset = _current_asset(db, dealership_id, BrandingAssetKind.PRIMARY, location_id)
    stored = store.get(asset.object_key) if asset is not None else None
    if asset is not None and stored is None:
        logger.error("branding: %s names %s, which the object store does not hold", asset.id, asset.object_key)
    if stored is None:
        raise NotFoundError("This location has no letterhead logo of its own.")
    return stored


def document_header_logo(
    db: Session,
    dealership: Dealership,
    *,
    store: ObjectStore | None = None,
    location_id: uuid.UUID | None = None,
) -> StoredObject | None:
    """The logo a document's header prints: the site's own letterhead logo
    when the document is printed for a location that has one (KAN-206),
    else the Betrieb's primary file — while the `document_header` placement
    is on. None prints the renderer's own no-logo mark. `store` defaults to
    the application's, asked for only when there is a file to read."""

    if not dealership.logo_on_document_header:
        return None
    asset = None
    if location_id is not None:
        asset = _current_asset(db, dealership.id, BrandingAssetKind.PRIMARY, location_id)
    if asset is None:
        asset = list_assets(db, dealership.id).get(BrandingAssetKind.PRIMARY)
    if asset is None:
        return None
    stored = (store or get_object_store()).get(asset.object_key)
    if stored is None:
        logger.error("branding: %s names %s, which the object store does not hold", asset.id, asset.object_key)
    return stored


# The preview's one line of content, in the Betrieb's correspondence
# language — documents never render in the UI language (ADR-051).
PREVIEW_TITLES: dict[SwissLanguage, str] = {
    SwissLanguage.DE: "Vorschau des Briefkopfs",
    SwissLanguage.FR: "Aperçu de l'en-tête",
    SwissLanguage.IT: "Anteprima dell'intestazione",
    SwissLanguage.EN: "Letterhead preview",
}
