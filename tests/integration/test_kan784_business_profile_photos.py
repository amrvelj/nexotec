"""KAN-226 / KAN-784 (ADR-209): a location's publishing imagery uploaded to
its Google Business Profile — queued by the worker, uploaded by the web
service beside the object store, upload only.

Fake Google (`tests/gbp_fakes.py`), invented values only.
"""

import datetime as dt
import logging
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.base import utcnow
from app.core.object_store import LocalDirectoryObjectStore
from app.core.outbox_model import OutboxMessage
from app.integration.models.google_business_profile import (
    GbpBindingStatus,
    GbpLocationBinding,
    GbpPhotoJob,
    GbpPhotoUpload,
)
from app.integration.services import gbp_photo_runner, gbp_photos, resilience
from app.integration.services.gbp_status import GbpPhotoStatusProvider
from app.platform.administration.services import publishing_profile
from app.platform.organisation.models.branding_asset import BrandingAsset, BrandingAssetKind
from app.platform.organisation.models.dealership import Location
from app.platform.public import location_channel_images, read_location_channel_image
from tests.cross_context.test_consumer_redelivery_idempotency import _business_profile_site, _make_dealership

_JPEG = b"\xff\xd8\xff\xe0invented-jpeg-bytes"


@pytest.fixture
def store(tmp_path):
    return LocalDirectoryObjectStore(tmp_path)


@pytest.fixture
def site(db_session, monkeypatch):
    fake, location, binding = _business_profile_site(db_session, monkeypatch)
    return fake, location, binding


def _image(db, store, location: Location, kind: BrandingAssetKind, *, primary=False, position=0) -> BrandingAsset:
    asset_id = uuid.uuid4()
    key = publishing_profile.image_key(location.tenant_id, location.id, kind, asset_id)
    store.put(key, _JPEG + asset_id.bytes, content_type="image/jpeg")
    asset = BrandingAsset(
        id=asset_id,
        dealership_id=location.tenant_id,
        location_id=location.id,
        kind=kind,
        object_key=key,
        mime_type="image/jpeg",
        width=1200,
        height=800,
        byte_size=len(_JPEG) + 16,
        position=position,
        is_primary=primary,
    )
    db.add(asset)
    db.commit()
    return asset


def _message(location: Location, event_type: str, changed=("imagery",), binding_id=None) -> OutboxMessage:
    payload = {"dealershipId": str(location.tenant_id), "locationId": str(location.id), "changedFields": list(changed)}
    if binding_id is not None:
        payload["bindingId"] = str(binding_id)
    return OutboxMessage(
        id=uuid.uuid4(),
        event_type=event_type,
        tenant_id=location.tenant_id,
        producer="platform",
        aggregate_type="publishing_profile",
        aggregate_id=uuid.uuid4(),
        payload=payload,
    )


def _queue(db, location: Location) -> int:
    queued = gbp_photos.queue_photo_pass(db, _message(location, gbp_photos.PUBLISHING_PROFILE_UPDATED))
    db.commit()
    return queued


def _run(db, store, **kwargs):
    claim = gbp_photos.claim_due_job(db, **kwargs)
    assert claim is not None
    return gbp_photos.run_photo_job(db, claim, store=store)


def _uploads(db) -> dict[uuid.UUID, set[str]]:
    rows = db.execute(select(GbpPhotoUpload.asset_id, GbpPhotoUpload.category)).all()
    out: dict[uuid.UUID, set[str]] = {}
    for asset_id, category in rows:
        out.setdefault(asset_id, set()).add(category)
    return out


def _created_categories(fake) -> list[str]:
    import json

    return [
        json.loads(r.content)["locationAssociation"]["category"]
        for r in fake.requests
        if str(r.url).split("?")[0].endswith("/media") and r.method == "POST"
    ]


def _job(db) -> GbpPhotoJob | None:
    db.expire_all()
    return db.scalar(select(GbpPhotoJob))


# --- the queue (the worker) -----------------------------------------------------------


def test_the_consumer_code_for_imagery_is_platforms():
    assert gbp_photos.IMAGERY_FIELD == publishing_profile.IMAGERY


def test_an_imagery_change_queues_one_pass_and_reads_no_image(db_session, site, monkeypatch):
    _fake, location, binding = site

    def _no_store():
        raise AssertionError("the worker read the object store")

    monkeypatch.setattr("app.platform.administration.services.location_channel_images.get_object_store", _no_store)
    monkeypatch.setattr("app.core.object_store.get_object_store", _no_store)

    assert _queue(db_session, location) == 1
    assert _queue(db_session, location) == 1  # the same row, due again

    job = _job(db_session)
    assert job.binding_id == binding.id and job.attempts == 0 and job.dead_at is None
    assert db_session.scalar(select(func.count()).select_from(GbpPhotoJob)) == 1
    assert job.generation == 1


def test_a_profile_change_without_imagery_queues_nothing(db_session, site):
    _fake, location, _binding = site
    message = _message(location, gbp_photos.PUBLISHING_PROFILE_UPDATED, changed=("descriptionDe",))
    assert gbp_photos.queue_photo_pass(db_session, message) == 0
    assert _job(db_session) is None


def test_a_location_without_a_live_binding_queues_nothing(db_session, site):
    _fake, location, binding = site
    binding.status = GbpBindingStatus.DISCONNECTED
    db_session.commit()
    assert _queue(db_session, location) == 0


def test_a_message_naming_two_businesses_is_refused(db_session, site):
    _fake, location, _binding = site
    message = _message(location, gbp_photos.PUBLISHING_PROFILE_UPDATED)
    message.tenant_id = uuid.uuid4()
    with pytest.raises(ValueError):
        gbp_photos.queue_photo_pass(db_session, message)


# --- the upload (the web service) -----------------------------------------------------


def test_every_image_is_uploaded_once_under_its_category(db_session, site, store):
    fake, location, _binding = site
    cover = _image(db_session, store, location, BrandingAssetKind.HERO, primary=True)
    other_hero = _image(db_session, store, location, BrandingAssetKind.HERO, position=1)
    premises = _image(db_session, store, location, BrandingAssetKind.PREMISES)
    team = _image(db_session, store, location, BrandingAssetKind.TEAM)
    _queue(db_session, location)

    result = _run(db_session, store)

    assert result.outcome == gbp_photos.PhotoPassOutcome.DONE and result.uploaded == 4
    assert _uploads(db_session) == {
        cover.id: {"COVER"},
        other_hero.id: {"ADDITIONAL"},
        premises.id: {"EXTERIOR"},
        team.id: {"TEAMS"},
    }
    # The cover goes first: it is what Google shows on the profile.
    assert _created_categories(fake)[0] == "COVER"
    assert _job(db_session) is None
    # The bytes sent are the stored image's.
    uploads = [r for r in fake.requests if "/upload/" in str(r.url)]
    assert {r.content for r in uploads} == {_JPEG + a.id.bytes for a in (cover, other_hero, premises, team)}

    # A second pass: nothing left to upload, and Google is not called at all.
    before = len(fake.requests)
    _queue(db_session, location)
    assert _run(db_session, store).uploaded == 0
    assert len(fake.requests) == before
    assert _job(db_session) is None


def test_a_new_primary_hero_becomes_the_cover_and_the_old_one_stays(db_session, site, store):
    fake, location, _binding = site
    first = _image(db_session, store, location, BrandingAssetKind.HERO, primary=True)
    second = _image(db_session, store, location, BrandingAssetKind.HERO, position=1)
    _queue(db_session, location)
    _run(db_session, store)

    first.is_primary = False
    db_session.flush()
    second.is_primary = True
    db_session.commit()
    _queue(db_session, location)
    result = _run(db_session, store)

    assert result.uploaded == 1
    assert _uploads(db_session) == {first.id: {"COVER"}, second.id: {"ADDITIONAL", "COVER"}}
    # Nothing is ever deleted at Google.
    assert not [r for r in fake.requests if r.method == "DELETE"]


def test_a_removed_image_stays_at_google_and_in_the_record(db_session, site, store):
    fake, location, _binding = site
    image = _image(db_session, store, location, BrandingAssetKind.PREMISES)
    _queue(db_session, location)
    _run(db_session, store)

    db_session.delete(db_session.get(BrandingAsset, image.id))
    db_session.commit()
    _queue(db_session, location)
    _run(db_session, store)

    assert image.id in _uploads(db_session)
    assert not [r for r in fake.requests if r.method == "DELETE"]


def test_an_image_missing_from_the_store_is_skipped(db_session, site, store, caplog):
    _fake, location, _binding = site
    image = _image(db_session, store, location, BrandingAssetKind.PREMISES)
    store.delete(image.object_key)
    _queue(db_session, location)

    with caplog.at_level(logging.ERROR):
        result = _run(db_session, store)

    assert result.uploaded == 0 and _uploads(db_session) == {}
    assert "does not hold" in caplog.text


def test_a_deactivated_location_is_skipped(db_session, site, store):
    fake, location, _binding = site
    _image(db_session, store, location, BrandingAssetKind.PREMISES)
    _queue(db_session, location)
    location.is_active = False
    db_session.commit()

    assert _run(db_session, store).outcome == gbp_photos.PhotoPassOutcome.DROPPED
    assert _uploads(db_session) == {} and _job(db_session) is None
    assert not [r for r in fake.requests if "/media" in str(r.url)]


def test_a_refused_grant_moves_the_binding_to_error(db_session, site, store):
    fake, location, binding = site
    _image(db_session, store, location, BrandingAssetKind.PREMISES)
    _queue(db_session, location)
    fake.refused_refresh_tokens.add("1//invented-refresh-redelivery")

    assert _run(db_session, store).outcome == gbp_photos.PhotoPassOutcome.GRANT_REFUSED

    db_session.expire_all()
    assert db_session.get(GbpLocationBinding, binding.id).status == GbpBindingStatus.ERROR
    assert _job(db_session) is None


def test_a_failure_backs_off_then_dead_letters_with_a_status_warning(db_session, site, store, caplog):
    fake, location, binding = site
    _image(db_session, store, location, BrandingAssetKind.PREMISES)
    _queue(db_session, location)
    fake.media_failure_status = 503
    now = utcnow()

    result = _run(db_session, store, now=now)

    assert result.outcome == gbp_photos.PhotoPassOutcome.RETRY
    job = _job(db_session)
    assert job.attempts == 1 and job.last_error_code == "provider_unavailable" and job.dead_at is None
    assert job.due_at > now
    assert gbp_photos.claim_due_job(db_session, now=now) is None  # not due before its backoff

    status = GbpPhotoStatusProvider()
    assert status.items(db_session, dealership_id=binding.tenant_id, now=now) == []

    at = now
    with caplog.at_level(logging.ERROR):
        for _ in range(gbp_photos.MAX_ATTEMPTS - 1):
            at = _job(db_session).due_at
            _run(db_session, store, now=at)

    job = _job(db_session)
    assert job.attempts == gbp_photos.MAX_ATTEMPTS and job.dead_at is not None
    assert "dead-lettered" in caplog.text
    assert gbp_photos.claim_due_job(db_session, now=at + dt.timedelta(days=30)) is None
    [item] = status.items(db_session, dealership_id=binding.tenant_id, now=at)
    assert item.state.value == "warning"
    assert item.message_key == "statusPage.items.googleBusinessProfilePhotos.failed"
    assert item.params == {"location": "Zürich"}
    assert status.items(db_session, dealership_id=uuid.uuid4(), now=at) == []

    # The next change queues it afresh; the warning goes. (The 503s opened
    # the client's breaker, as they should; Google is back.)
    fake.media_failure_status = None
    resilience._CIRCUITS.clear()
    _queue(db_session, location)
    assert status.items(db_session, dealership_id=binding.tenant_id, now=at) == []
    assert _run(db_session, store).uploaded == 1


def test_one_image_google_refuses_never_holds_the_others(db_session, site, store):
    """KAN-784 review: Google refusing one image (its size or format) is that
    image's refusal — recorded, not retried, named on the status page — and
    the location's other photos still go."""

    fake, location, binding = site
    refused = _image(db_session, store, location, BrandingAssetKind.HERO, primary=True)
    other = _image(db_session, store, location, BrandingAssetKind.PREMISES)
    fake.refused_media.add(_JPEG + refused.id.bytes)
    _queue(db_session, location)

    result = _run(db_session, store)

    assert result.outcome == gbp_photos.PhotoPassOutcome.DONE and result.uploaded == 1
    assert _job(db_session) is None
    rows = {r.asset_id: r for r in db_session.scalars(select(GbpPhotoUpload)).all()}
    assert rows[refused.id].refusal_code == "provider_refused_request" and rows[refused.id].google_media_name is None
    assert rows[other.id].google_media_name is not None and rows[other.id].refusal_code is None

    status = GbpPhotoStatusProvider()
    [item] = status.items(db_session, dealership_id=binding.tenant_id, now=utcnow())
    assert item.message_key == "statusPage.items.googleBusinessProfilePhotos.refused"
    assert item.params == {"location": "Zürich", "count": 1}
    assert item.link == "/administration/publishing"

    # Not sent again on the next pass.
    before = len(fake.requests)
    _queue(db_session, location)
    assert _run(db_session, store).uploaded == 0
    assert len(fake.requests) == before

    # The dealer removes it in Nexotec: the refusal is no longer shown.
    db_session.delete(db_session.get(BrandingAsset, refused.id))
    db_session.commit()
    _queue(db_session, location)
    _run(db_session, store)
    assert status.items(db_session, dealership_id=binding.tenant_id, now=utcnow()) == []


def test_an_unexpected_failure_is_counted_and_dead_letters(db_session, site, store, monkeypatch, caplog):
    """KAN-784 review: a defect (here the store raising) backs off and
    dead-letters like any failure — never retried forever."""

    _fake, location, _binding = site
    _image(db_session, store, location, BrandingAssetKind.TEAM)
    _queue(db_session, location)

    def _broken(*args, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(gbp_photos, "read_location_channel_image", _broken)
    now = utcnow()
    with caplog.at_level(logging.ERROR):
        assert _run(db_session, store, now=now).outcome == gbp_photos.PhotoPassOutcome.RETRY
    job = _job(db_session)
    assert job.attempts == 1 and job.last_error_code == "internal_error"
    assert "failed unexpectedly" in caplog.text
    for _ in range(gbp_photos.MAX_ATTEMPTS - 1):
        _run(db_session, store, now=_job(db_session).due_at)
    assert _job(db_session).dead_at is not None


def test_the_backoff_doubles_and_is_capped():
    assert gbp_photos.backoff(1) == dt.timedelta(minutes=5)
    assert gbp_photos.backoff(2) == dt.timedelta(minutes=10)
    assert gbp_photos.backoff(20) == dt.timedelta(hours=6)


def test_a_pass_never_drops_a_job_queued_again_while_it_ran(db_session, site, store):
    _fake, location, _binding = site
    _image(db_session, store, location, BrandingAssetKind.PREMISES)
    _queue(db_session, location)
    claim = gbp_photos.claim_due_job(db_session)
    # A claimed job is not due again within its lease.
    assert gbp_photos.claim_due_job(db_session) is None

    _queue(db_session, location)  # a new image arrived meanwhile
    gbp_photos.run_photo_job(db_session, claim, store=store)

    job = _job(db_session)
    assert job is not None and job.due_at <= utcnow()


def test_the_runner_runs_every_due_job_once(db_session, engine, site, store):
    _fake, location, _binding = site
    _image(db_session, store, location, BrandingAssetKind.TEAM)
    _queue(db_session, location)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    assert gbp_photo_runner.run_once(factory, store=store) == 1
    assert gbp_photo_runner.run_once(factory, store=store) == 0
    db_session.expire_all()
    assert len(_uploads(db_session)) == 1


# --- the platform read ------------------------------------------------------------------


def test_the_imagery_read_is_tenant_scoped(db_session, site, store):
    _fake, location, _binding = site
    image = _image(db_session, store, location, BrandingAssetKind.HERO, primary=True)
    other = _make_dealership(db_session)

    imagery = location_channel_images(db_session, dealership_id=location.tenant_id, location_id=location.id)
    assert imagery.is_active and [(i.asset_id, i.kind, i.is_primary) for i in imagery.images] == [
        (image.id, "hero", True)
    ]
    assert location_channel_images(db_session, dealership_id=other.id, location_id=location.id) is None
    assert (
        read_location_channel_image(
            db_session, dealership_id=other.id, location_id=location.id, asset_id=image.id, store=store
        )
        is None
    )
    stored = read_location_channel_image(
        db_session, dealership_id=location.tenant_id, location_id=location.id, asset_id=image.id, store=store
    )
    assert stored.data == _JPEG + image.id.bytes
