"""The «Fotos» line of the Business Profile card (KAN-821; Dealer
Administration FR-A-35, D-A-32; ADR-209).

Per binding, what the photo upload (`gbp_photos`) has done with the
location's imagery — read from Nexotec's own records, never from Google:

- `uploaded` — the photos Nexotec put at this Google location
  (`integration_gbp_photo_upload` rows with Google's media name). An image
  removed in Nexotec stays at Google (Anto, 2026-11-21), so it still counts;
  a hero that became the cover was uploaded twice and counts twice, as it is
  two photos at Google;
- `refused` — the images Google refused (size, format); not sent again until
  the image is replaced;
- `waiting` — the location's images the next pass would upload, judged by
  the pass's own rule (`gbp_photos.pending_uploads`). Only for an active location:
  a deactivated one uploads nothing (ADR-199), so none of its images waits;
- `stopped` — the pass was dead-lettered after its last attempt
  (`integration_gbp_photo_job.dead_at`); the status page names it, and the
  next change to the location queues it again.

Only an active binding has a summary: a binding without a Google location,
or one whose grant was refused, uploads nothing.
"""

import dataclasses
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.integration.models.google_business_profile import (
    GbpBindingStatus,
    GbpLocationBinding,
    GbpPhotoJob,
    GbpPhotoUpload,
)
from app.integration.services.gbp_photos import pending_uploads
from app.platform.public import location_channel_images


@dataclasses.dataclass(frozen=True)
class PhotoSummary:
    uploaded: int
    refused: int
    waiting: int
    stopped: bool


def photo_summaries(
    db: Session, *, dealership_id: uuid.UUID, bindings: list[GbpLocationBinding]
) -> dict[uuid.UUID, PhotoSummary]:
    """The photo summary of each active binding of the Betrieb, by binding id."""

    active = [b for b in bindings if b.status == GbpBindingStatus.ACTIVE and b.tenant_id == dealership_id]
    if not active:
        return {}
    ids = [b.id for b in active]
    counts = db.execute(
        select(
            GbpPhotoUpload.binding_id,
            func.count(GbpPhotoUpload.google_media_name),
            func.count(GbpPhotoUpload.refusal_code),
        )
        .where(GbpPhotoUpload.tenant_id == dealership_id, GbpPhotoUpload.binding_id.in_(ids))
        .group_by(GbpPhotoUpload.binding_id)
    ).all()
    by_binding = {binding_id: (uploaded, refused) for binding_id, uploaded, refused in counts}
    stopped = set(
        db.scalars(
            select(GbpPhotoJob.binding_id).where(
                GbpPhotoJob.tenant_id == dealership_id,
                GbpPhotoJob.binding_id.in_(ids),
                GbpPhotoJob.dead_at.is_not(None),
            )
        ).all()
    )
    summaries: dict[uuid.UUID, PhotoSummary] = {}
    for binding in active:
        imagery = location_channel_images(db, dealership_id=dealership_id, location_id=binding.location_id)
        waiting = len(pending_uploads(db, binding, imagery.images)) if imagery is not None and imagery.is_active else 0
        uploaded, refused = by_binding.get(binding.id, (0, 0))
        summaries[binding.id] = PhotoSummary(
            uploaded=uploaded, refused=refused, waiting=waiting, stopped=binding.id in stopped
        )
    return summaries
