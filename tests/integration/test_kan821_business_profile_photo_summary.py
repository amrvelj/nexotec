"""KAN-821: the Business Profile card's «Fotos» line — per bound location,
how many images the upload put at Google, how many Google refused, how many
wait for the next pass and whether the pass stopped, read from Nexotec's own
records (`gbp_photo_summary`), never from Google.

Fake Google (`tests/gbp_fakes.py`), invented values only.
"""

import uuid

import pytest

from app.core.object_store import LocalDirectoryObjectStore
from app.integration.models.google_business_profile import GbpBindingStatus
from app.integration.services import gbp_photo_summary, gbp_photos
from app.platform.organisation.models.branding_asset import BrandingAsset, BrandingAssetKind
from tests.cross_context.test_consumer_redelivery_idempotency import _business_profile_site
from tests.identapi_support import make_user
from tests.integration import test_kan226_business_profile as pr2
from tests.integration.test_kan784_business_profile_photos import _JPEG, _image, _job, _queue, _run
from tests.inventory.test_inventory_invoicing_gate import _make_dealership


@pytest.fixture
def store(tmp_path):
    return LocalDirectoryObjectStore(tmp_path)


@pytest.fixture
def site(db_session, monkeypatch):
    return _business_profile_site(db_session, monkeypatch)


def _summary(db, binding) -> gbp_photo_summary.PhotoSummary | None:
    db.expire_all()
    return gbp_photo_summary.photo_summaries(db, dealership_id=binding.tenant_id, bindings=[binding]).get(binding.id)


def test_uploaded_refused_and_waiting_are_counted_without_calling_google(db_session, site, store):
    fake, location, binding = site
    refused = _image(db_session, store, location, BrandingAssetKind.HERO, primary=True)
    _image(db_session, store, location, BrandingAssetKind.PREMISES)
    _image(db_session, store, location, BrandingAssetKind.TEAM)
    fake.refused_media.add(_JPEG + refused.id.bytes)

    before = len(fake.requests)
    assert _summary(db_session, binding) == gbp_photo_summary.PhotoSummary(
        uploaded=0, refused=0, waiting=3, stopped=False
    )
    assert len(fake.requests) == before  # the summary never asks Google

    _queue(db_session, location)
    _run(db_session, store)
    assert _summary(db_session, binding) == gbp_photo_summary.PhotoSummary(
        uploaded=2, refused=1, waiting=0, stopped=False
    )

    # A new image waits until the next pass.
    _image(db_session, store, location, BrandingAssetKind.HERO, position=1)
    assert _summary(db_session, binding).waiting == 1


def test_a_removed_image_still_counts_as_at_google(db_session, site, store):
    _fake, location, binding = site
    asset = _image(db_session, store, location, BrandingAssetKind.PREMISES)
    _queue(db_session, location)
    _run(db_session, store)

    db_session.delete(db_session.get(BrandingAsset, asset.id))
    db_session.commit()

    # Nexotec never deletes at Google (Anto, 2026-11-21): the photo is still there.
    assert _summary(db_session, binding) == gbp_photo_summary.PhotoSummary(
        uploaded=1, refused=0, waiting=0, stopped=False
    )


def test_a_dead_lettered_pass_is_stopped(db_session, site, store):
    fake, location, binding = site
    _image(db_session, store, location, BrandingAssetKind.PREMISES)
    _queue(db_session, location)
    fake.media_failure_status = 503
    for _ in range(gbp_photos.MAX_ATTEMPTS):
        _run(db_session, store, now=_job(db_session).due_at)

    assert _summary(db_session, binding) == gbp_photo_summary.PhotoSummary(
        uploaded=0, refused=0, waiting=1, stopped=True
    )


def test_a_deactivated_location_has_nothing_waiting(db_session, site, store):
    _fake, location, binding = site
    _image(db_session, store, location, BrandingAssetKind.PREMISES)
    location.is_active = False
    db_session.commit()

    assert _summary(db_session, binding).waiting == 0


def test_only_an_active_binding_of_the_betrieb_has_a_summary(db_session, site, store):
    _fake, location, binding = site
    _image(db_session, store, location, BrandingAssetKind.PREMISES)

    for status in (GbpBindingStatus.PENDING, GbpBindingStatus.ERROR, GbpBindingStatus.DISCONNECTED):
        binding.status = status
        db_session.commit()
        assert _summary(db_session, binding) is None

    binding.status = GbpBindingStatus.ACTIVE
    db_session.commit()
    other = gbp_photo_summary.photo_summaries(db_session, dealership_id=uuid.uuid4(), bindings=[binding])
    assert other == {}


def test_the_bindings_list_carries_the_photos(client, db_session, site, store):
    fake, location, binding = site
    refused = _image(db_session, store, location, BrandingAssetKind.HERO, primary=True)
    _image(db_session, store, location, BrandingAssetKind.PREMISES)
    fake.refused_media.add(_JPEG + refused.id.bytes)
    _queue(db_session, location)
    _run(db_session, store)
    _image(db_session, store, location, BrandingAssetKind.TEAM)
    manager = make_user(db_session, tenant_id=binding.tenant_id, email="chef-fotos@example.test")

    base = f"/v1/dealerships/{binding.tenant_id}/business-profile/bindings"
    response = client.get(base, headers=pr2._as(manager))
    assert response.status_code == 200, response.text
    [row] = response.json()["items"]
    assert row["photos"] == {"uploaded": 1, "refused": 1, "waiting": 1, "stopped": False}

    binding.status = GbpBindingStatus.PENDING
    db_session.commit()
    [row] = client.get(base, headers=pr2._as(manager)).json()["items"]
    assert row["photos"] is None

    outsider_betrieb = _make_dealership(db_session)
    outsider = make_user(db_session, tenant_id=outsider_betrieb.id, email="chef-fremd@example.test")
    assert client.get(base, headers=pr2._as(outsider)).status_code == 404
