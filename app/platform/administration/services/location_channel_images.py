"""A location's publishing imagery as a channel publishes it (KAN-226,
KAN-784; Dealer Administration PRD FR-A-35, D-A-32; ADR-209): what the
Google Business Profile photo upload reads of a site.

Two reads, plain values only (ADR-162): never a mapped class crosses
`platform.public`.

- `location_channel_images` lists the site's imagery (`premises`, `team`,
  `hero`; KAN-225): each image's id, kind, primary flag, position and type.
  It reads the database only, so a worker may call it.
- `read_location_channel_image` hands one image's bytes out of the object
  store. The store is the web service's disk while it is a disk (ADR-081 as
  amended, KAN-738), so only code running beside it calls this — the web
  service's photo runner, never a worker's consumer.

Tenant-scoped: a location of another Betrieb reads as `None`, as a missing one
does, and an image is read only through the location and the Betrieb it
belongs to, so a consumer cannot learn that another exists.
"""

import dataclasses
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.object_store import ObjectStore, StoredObject, get_object_store
from app.platform.organisation.models.branding_asset import IMAGERY_KINDS, BrandingAsset
from app.platform.organisation.models.dealership import Location

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ChannelImage:
    asset_id: uuid.UUID
    # `premises` · `team` · `hero`
    kind: str
    is_primary: bool
    position: int
    mime_type: str


@dataclasses.dataclass(frozen=True)
class LocationChannelImagery:
    location_id: uuid.UUID
    # A deactivated location publishes nothing (ADR-199).
    is_active: bool
    images: tuple[ChannelImage, ...]


def _imagery(dealership_id: uuid.UUID, location_id: uuid.UUID):
    return select(BrandingAsset).where(
        BrandingAsset.dealership_id == dealership_id,
        BrandingAsset.location_id == location_id,
        BrandingAsset.kind.in_(IMAGERY_KINDS),
    )


def location_channel_images(
    db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID
) -> LocationChannelImagery | None:
    """The site's imagery, by kind and position, and whether the site is
    active; `None` when the location is not one of this Betrieb's."""

    location = db.get(Location, location_id)
    if location is None or location.tenant_id != dealership_id:
        return None
    rows = db.scalars(
        _imagery(dealership_id, location_id).order_by(BrandingAsset.kind, BrandingAsset.position, BrandingAsset.id)
    ).all()
    images = tuple(
        ChannelImage(
            asset_id=row.id,
            kind=row.kind.value,
            is_primary=bool(row.is_primary),
            position=row.position or 0,
            mime_type=row.mime_type,
        )
        for row in rows
    )
    return LocationChannelImagery(location_id=location.id, is_active=bool(location.is_active), images=images)


def read_location_channel_image(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    asset_id: uuid.UUID,
    store: ObjectStore | None = None,
) -> StoredObject | None:
    """One image of the site's imagery, or `None`: not this Betrieb's or this
    location's, removed meanwhile, or a file the store does not hold (logged
    at ERROR — the stored-objects check reports it too)."""

    row = db.scalar(_imagery(dealership_id, location_id).where(BrandingAsset.id == asset_id))
    if row is None:
        return None
    stored = (store or get_object_store()).get(row.object_key)
    if stored is None:
        logger.error("channel image %s names %s, which the object store does not hold", row.id, row.object_key)
    return stored
