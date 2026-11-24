"""The Google Business Profile photo upload (KAN-226, KAN-784; Dealer
Administration FR-A-35, D-A-32; ADR-209): a location's publishing imagery,
Nexotec → Google, upload only.

**Why two halves.** The images live in the object store, which is the web
service's persistent disk while it is a disk (ADR-081 as amended, KAN-738):
the worker cannot read it in production. So the worker only learns that a
location's photos may have changed and queues a pass; the web service
uploads.

- **Queue (worker).** `queue_photo_pass` is the consumer of
  `platform.publishing_profile.updated` (when its `changedFields` names the
  imagery), `platform.location.updated` (a location activated again shows
  what was added meanwhile) and Integration's own
  `integration.gbp_location_binding.activated` (a new link gets its photos).
  It upserts one `integration_gbp_photo_job` row per live binding of the
  location, due now, in the consumer's own transaction — idempotent: a
  redelivery finds the row and leaves it due. It never reads the store.
- **Upload (web).** `gbp_photo_runner` claims due rows and calls
  `run_photo_job` for each: every image of the location not yet in
  `integration_gbp_photo_upload` for this binding and category is read from
  the store and uploaded (`adapter.upload_photo`, under `call_capability`);
  each upload is recorded and committed at once, so a failure part-way never
  uploads a photo twice. No image left to upload → no Google call at all.

**The mapping (ADR-209):** the primary hero → `COVER`; premises →
`EXTERIOR`; team → `TEAMS`; any other hero → `ADDITIONAL`. Each image goes to
a Google location once; a hero that becomes the primary one is the one
exception, uploaded again as the new `COVER`.

**Never deletes at Google** (Anto, 2026-11-21: «just because a dealer is not
anymore on Nexotec he can and should still have his Google profile»): an image
removed in Nexotec stays at Google, and so does an earlier cover.

**Failures.** A refused grant moves the binding to `error`, as the push does
(`gbp_link.mark_grant_refused`), and drops the job: nothing is retried until
the manager reconnects (the binding's activation queues a pass again). Google
refusing one image (a 400/404/422 — its size or format) is that image's
refusal: recorded, named on the status page, not sent again, and the pass
goes on with the others. Any other failure — Google's, the gateway's or a
defect of ours — backs off (`BACKOFF`); after `MAX_ATTEMPTS` the job is
dead-lettered (`dead_at`), logged at ERROR, and the Betrieb's status page
shows a warning for the location (`GbpPhotoStatusProvider`, rule 10) until
the next change queues it afresh. A deactivated location is skipped
(ADR-199) and its job dropped.
"""

import dataclasses
import datetime as dt
import enum
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.object_store import ObjectStore
from app.core.outbox_model import OutboxMessage
from app.integration.adapters.google_business_profile import (
    GBP_CAPABILITY,
    GoogleBusinessProfileAdapter,
    PhotoRefusedError,
)
from app.integration.errors import (
    ProviderAccountRefusedError,
    ProviderConfigurationError,
    ProviderGatewayError,
    ProviderRejectedError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from app.integration.models.google_business_profile import (
    GbpBindingStatus,
    GbpLocationBinding,
    GbpPhotoJob,
    GbpPhotoUpload,
)
from app.integration.services import gbp_link
from app.integration.services.gateway import call_capability
from app.integration.services.resilience import CircuitOpenError
from app.platform.public import ChannelImage, location_channel_images, read_location_channel_image

logger = logging.getLogger(__name__)

_PURPOSE = "business_profile_photos"

PUBLISHING_PROFILE_UPDATED = "platform.publishing_profile.updated"
LOCATION_UPDATED = "platform.location.updated"
# Platform's `changedFields` code for the imagery (`publishing_profile.IMAGERY`;
# a test holds them equal).
IMAGERY_FIELD = "imagery"

MAX_ATTEMPTS = 8
# The wait before attempt n + 1 after n failed: 5 minutes, doubling, at most
# 6 hours — eight attempts span about a day.
_FIRST_BACKOFF = dt.timedelta(minutes=5)
_MAX_BACKOFF = dt.timedelta(hours=6)


def backoff(attempts: int) -> dt.timedelta:
    return min(_FIRST_BACKOFF * (2 ** max(attempts - 1, 0)), _MAX_BACKOFF)


def photo_category(image: ChannelImage) -> str | None:
    """Google's v4 media category for one of a location's images."""

    if image.kind == "hero":
        return "COVER" if image.is_primary else "ADDITIONAL"
    if image.kind == "premises":
        return "EXTERIOR"
    if image.kind == "team":
        return "TEAMS"
    return None


# -- the queue (the worker) ------------------------------------------------------------


def _ids(message: OutboxMessage) -> tuple[uuid.UUID, uuid.UUID | None]:
    dealership_id = uuid.UUID(message.payload["dealershipId"])
    if message.tenant_id is not None and message.tenant_id != dealership_id:
        raise ValueError(f"{message.event_type} message {message.id} names two businesses.")
    location = message.payload.get("locationId")
    return dealership_id, uuid.UUID(location) if location else None


def _queue(db: Session, binding: GbpLocationBinding, *, now: dt.datetime) -> None:
    job = db.scalar(select(GbpPhotoJob).where(GbpPhotoJob.binding_id == binding.id))
    if job is None:
        db.add(GbpPhotoJob(binding_id=binding.id, tenant_id=binding.tenant_id, due_at=now, attempts=0, generation=0))
        return
    job.due_at = now
    job.generation += 1
    job.attempts = 0
    job.dead_at = None
    job.last_error_code = None


def queue_photo_pass(db: Session, message: OutboxMessage, *, now: dt.datetime | None = None) -> int:
    """Queues a photo pass for every live binding of the message's location.
    Returns how many were queued. Writes in the caller's (the consumer's)
    transaction; reads no image and calls nobody."""

    if message.event_type == PUBLISHING_PROFILE_UPDATED and IMAGERY_FIELD not in (
        message.payload.get("changedFields") or []
    ):
        return 0
    dealership_id, location_id = _ids(message)
    if location_id is None:
        return 0
    at = now or utcnow()
    bindings = db.scalars(
        select(GbpLocationBinding)
        .where(
            GbpLocationBinding.tenant_id == dealership_id,
            GbpLocationBinding.location_id == location_id,
            GbpLocationBinding.status == GbpBindingStatus.ACTIVE,
            GbpLocationBinding.google_location_name.is_not(None),
        )
        # The binding's lock orders two consumers queuing the same binding.
        .with_for_update()
    ).all()
    for binding in bindings:
        _queue(db, binding, now=at)
    return len(bindings)


# -- the upload (the web service) ------------------------------------------------------


class PhotoPassOutcome(str, enum.Enum):
    DONE = "done"
    # The binding is no longer live, or its location is gone or deactivated.
    DROPPED = "dropped"
    GRANT_REFUSED = "grant_refused"
    RETRY = "retry"
    DEAD = "dead"


@dataclasses.dataclass(frozen=True)
class PhotoPassResult:
    outcome: PhotoPassOutcome
    uploaded: int = 0


def _error_code(error: Exception) -> str:
    if isinstance(error, CircuitOpenError):
        return "circuit_open"
    if isinstance(error, ProviderTimeoutError):
        return "provider_timeout"
    if isinstance(error, ProviderTransportError):
        return "provider_unavailable"
    if isinstance(error, ProviderConfigurationError):
        return "client_not_configured"
    if isinstance(error, ProviderRequestError):
        return "provider_refused_request"
    if isinstance(error, ProviderRejectedError):
        return "provider_rejected"
    if isinstance(error, ProviderGatewayError):
        return "provider_error"
    return "internal_error"


@contextmanager
def _google(db: Session) -> Iterator[GoogleBusinessProfileAdapter]:
    connection = gbp_link.client_connection(db)
    if connection is None:
        raise ProviderConfigurationError("Nexotec's Google Business Profile client is not set up.")
    with call_capability(db, connection=connection, capability=GBP_CAPABILITY, purpose=_PURPOSE) as raw:
        yield cast(GoogleBusinessProfileAdapter, raw)


def _pending(
    db: Session, binding: GbpLocationBinding, images: tuple[ChannelImage, ...]
) -> list[tuple[ChannelImage, str]]:
    """The images still to upload, with their category. An image already at
    this Google location is not sent again under another category — with one
    exception: a hero that became the primary one goes again as the `COVER`
    (ADR-209), since the cover is what Google shows on the profile."""

    rows = db.execute(
        select(GbpPhotoUpload.asset_id, GbpPhotoUpload.category).where(GbpPhotoUpload.binding_id == binding.id)
    ).all()
    uploaded = {asset_id for asset_id, _category in rows}
    covers = {asset_id for asset_id, category in rows if category == "COVER"}
    pending: list[tuple[ChannelImage, str]] = []
    for image in images:
        category = photo_category(image)
        if category is None:
            continue
        if image.asset_id in (covers if category == "COVER" else uploaded):
            continue
        pending.append((image, category))
    # The cover first: it is what Google shows on the profile.
    pending.sort(key=lambda pair: pair[1] != "COVER")
    return pending


def _forget_refusals_of_removed_images(
    db: Session, binding: GbpLocationBinding, images: tuple[ChannelImage, ...]
) -> None:
    """A refusal names an image the dealer can replace; once it is gone from
    the location, its refusal is no longer something to show. Uploaded rows
    stay: the photo stays at Google."""

    current = {image.asset_id for image in images}
    refused = db.scalars(
        select(GbpPhotoUpload).where(GbpPhotoUpload.binding_id == binding.id, GbpPhotoUpload.refusal_code.is_not(None))
    ).all()
    for row in refused:
        if row.asset_id not in current:
            db.delete(row)
    db.commit()


def _upload_pending(
    db: Session, binding: GbpLocationBinding, pending: list[tuple[ChannelImage, str]], *, store: ObjectStore | None
) -> int:
    uploaded = 0
    account_name = cast(str, binding.google_account_name)
    location_name = cast(str, binding.google_location_name)
    binding_id, tenant_id, location_id = binding.id, binding.tenant_id, binding.location_id
    with _google(db) as adapter:
        token = adapter.access_token(binding_id=binding_id)
        for image, category in pending:
            stored = read_location_channel_image(
                db, dealership_id=tenant_id, location_id=location_id, asset_id=image.asset_id, store=store
            )
            if stored is None:
                continue  # removed meanwhile, or missing from the store (logged by platform)
            try:
                media = adapter.upload_photo(
                    access_token=token,
                    account_name=account_name,
                    location_name=location_name,
                    content=stored.data,
                    content_type=stored.content_type,
                    category=category,
                )
            except PhotoRefusedError:
                # Google refused this image itself (a 400/404/422 on the steps
                # that carry its bytes: its size, its format). Retrying sends
                # the same bytes again, and holding the pass would keep the
                # location's other photos from Google: the refusal is recorded,
                # shown on the status page, and the pass goes on (KAN-784
                # review). A refusal of the location itself (`startUpload`, the
                # binding's names) is a plain ProviderRequestError: the job
                # fails, backs off and dead-letters.
                db.rollback()
                db.add(
                    GbpPhotoUpload(
                        binding_id=binding_id,
                        tenant_id=tenant_id,
                        asset_id=image.asset_id,
                        category=category,
                        refusal_code="provider_refused_request",
                        uploaded_at=utcnow(),
                    )
                )
                db.commit()
                logger.warning(
                    "business profile photos: Google refused image %s for binding %s",
                    image.asset_id,
                    binding_id,
                    extra={"bindingId": str(binding_id), "assetId": str(image.asset_id)},
                )
                continue
            db.add(
                GbpPhotoUpload(
                    binding_id=binding_id,
                    tenant_id=tenant_id,
                    asset_id=image.asset_id,
                    category=category,
                    google_media_name=media.name,
                    uploaded_at=utcnow(),
                )
            )
            # At once: a later failure must never upload this photo again.
            db.commit()
            uploaded += 1
    return uploaded


# A claimed job is not due again before this, so no other pass takes it while
# this one runs; a pass cut short by a restart is taken again after it.
CLAIM_LEASE = dt.timedelta(minutes=15)


@dataclasses.dataclass(frozen=True)
class PhotoJobClaim:
    job_id: uuid.UUID
    binding_id: uuid.UUID
    generation: int


def claim_due_job(
    db: Session, *, now: dt.datetime | None = None, skip: set[uuid.UUID] | None = None
) -> PhotoJobClaim | None:
    """Claims the next due job (`FOR UPDATE SKIP LOCKED`), moves it a lease
    ahead and commits, so the row lock is held only for the claim itself."""

    at = now or utcnow()
    query = select(GbpPhotoJob).where(GbpPhotoJob.dead_at.is_(None), GbpPhotoJob.due_at <= at)
    if skip:
        query = query.where(GbpPhotoJob.id.not_in(skip))
    job = db.scalar(query.order_by(GbpPhotoJob.due_at, GbpPhotoJob.id).limit(1).with_for_update(skip_locked=True))
    if job is None:
        db.rollback()
        return None
    claim = PhotoJobClaim(job_id=job.id, binding_id=job.binding_id, generation=job.generation)
    job.due_at = at + CLAIM_LEASE
    db.commit()
    return claim


def _locked_job(db: Session, claim: PhotoJobClaim) -> GbpPhotoJob | None:
    """The claimed job, locked, while it is still the generation claimed —
    None when a consumer queued it again meanwhile (it is due anew, and the
    next pass uploads what changed) or it is gone."""

    job = db.scalar(select(GbpPhotoJob).where(GbpPhotoJob.id == claim.job_id).with_for_update())
    if job is None or job.generation != claim.generation:
        return None
    return job


def _settle_done(db: Session, claim: PhotoJobClaim) -> None:
    job = _locked_job(db, claim)
    if job is not None:
        db.delete(job)
    db.commit()


def run_photo_job(
    db: Session,
    claim: PhotoJobClaim,
    *,
    store: ObjectStore | None = None,
    now: dt.datetime | None = None,
) -> PhotoPassResult:
    """One photo pass for one claimed job. Commits each upload, then settles
    the job: deleted when the pass is done or nothing is left to do,
    rescheduled or dead-lettered on a failure — each only while the job is
    still the generation claimed."""

    binding = db.get(GbpLocationBinding, claim.binding_id)
    if (
        binding is None
        or binding.status != GbpBindingStatus.ACTIVE
        or not binding.google_location_name
        or not binding.google_account_name
    ):
        _settle_done(db, claim)
        return PhotoPassResult(PhotoPassOutcome.DROPPED)
    try:
        imagery = location_channel_images(db, dealership_id=binding.tenant_id, location_id=binding.location_id)
        if imagery is None or not imagery.is_active:
            _settle_done(db, claim)
            return PhotoPassResult(PhotoPassOutcome.DROPPED)
        _forget_refusals_of_removed_images(db, binding, imagery.images)
        pending = _pending(db, binding, imagery.images)
        if not pending:
            _settle_done(db, claim)
            return PhotoPassResult(PhotoPassOutcome.DONE)
        uploaded = _upload_pending(db, binding, pending, store=store)
    except ProviderAccountRefusedError:
        gbp_link.mark_grant_refused(db, binding)
        _settle_done(db, claim)
        return PhotoPassResult(PhotoPassOutcome.GRANT_REFUSED)
    except ProviderGatewayError as error:
        # Google's or the gateway's failure: back off, then dead-letter.
        db.rollback()
        return _failed(db, claim, error=error, now=now or utcnow())
    except Exception as error:
        # Anything else is a defect (the store, the database, an adapter bug):
        # logged with its traceback, and counted like any failure, so it too
        # backs off and dead-letters instead of retrying forever (rule 10).
        logger.exception(
            "business profile photos: binding %s failed unexpectedly",
            claim.binding_id,
            extra={"bindingId": str(claim.binding_id)},
        )
        db.rollback()
        return _failed(db, claim, error=error, now=now or utcnow())
    _settle_done(db, claim)
    return PhotoPassResult(PhotoPassOutcome.DONE, uploaded=uploaded)


def _failed(db: Session, claim: PhotoJobClaim, *, error: Exception, now: dt.datetime) -> PhotoPassResult:
    code = _error_code(error)
    job = _locked_job(db, claim)
    if job is None:
        db.commit()
        logger.warning(
            "business profile photos: binding %s failed (%s); queued again meanwhile",
            claim.binding_id,
            code,
            extra={"bindingId": str(claim.binding_id), "errorCode": code},
        )
        return PhotoPassResult(PhotoPassOutcome.RETRY)
    job.attempts += 1
    job.last_error_code = code
    attempts = job.attempts
    if attempts >= MAX_ATTEMPTS:
        job.dead_at = now
        db.commit()
        logger.error(
            "business profile photos: binding %s dead-lettered after %d attempts (%s)",
            claim.binding_id,
            attempts,
            code,
            extra={"bindingId": str(claim.binding_id), "errorCode": code},
        )
        return PhotoPassResult(PhotoPassOutcome.DEAD)
    job.due_at = now + backoff(attempts)
    db.commit()
    logger.warning(
        "business profile photos: binding %s failed (%s), attempt %d of %d",
        claim.binding_id,
        code,
        attempts,
        MAX_ATTEMPTS,
        extra={"bindingId": str(claim.binding_id), "errorCode": code},
    )
    return PhotoPassResult(PhotoPassOutcome.RETRY)
