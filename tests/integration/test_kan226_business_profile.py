"""KAN-226 PR-2 (D-A-32, FR-A-35): binding a Nexotec location to Google
Business Profile — Nexotec's OAuth client as the one platform connection, the
link flow (single-use hashed state, PKCE), the location pick, disconnect, and
the adapter's reads, PATCH with its mask whitelist and the three-step photo
upload.

Against the recorded fixture only (`tests/gbp_fakes.py`,
`tests/fixtures/google_business_profile_recorded.json`, invented values):
nothing here has run against Google.
"""

import datetime as dt
import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.integration.adapters import google_business_profile as gbp
from app.integration.errors import ProviderAccountRefusedError, ProviderRequestError
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.connection import ConnectionEnvironment, ConnectionScope, IntegrationConnection
from app.integration.models.google_business_profile import (
    GBP_PROVIDER_CODE,
    GbpBindingStatus,
    GbpLinkState,
    GbpLocationBinding,
)
from app.integration.models.provider import IntegrationProvider
from app.integration.public import oldest_location_label_age_seconds, refresh_location_labels
from app.integration.services import gbp_link, resilience
from app.integration.services.gateway import call_capability
from app.platform.organisation.models.dealership import Location
from tests import gbp_fakes
from tests.gbp_fakes import ACCOUNT_1, ACCOUNT_2, CLIENT_ID, CLIENT_SECRET, LOCATION_1, LOCATION_2, LOCATION_3
from tests.identapi_support import make_user
from tests.inventory.test_inventory_invoicing_gate import _make_dealership
from tests.membership_support import bearer, token

CALLBACK = "/v1/integrations/business-profile/link-callback"
_MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "integration"


@pytest.fixture
def google(monkeypatch):
    fake, secrets_store = gbp_fakes.install(monkeypatch)
    yield fake, secrets_store
    resilience._CIRCUITS.clear()


@pytest.fixture
def client_connection(db_session, google):
    """Nexotec's OAuth client at Google: the platform-scope connection, its secret in the fake store."""

    _, secrets_store = google
    provider = IntegrationProvider(
        provider_code=GBP_PROVIDER_CODE, category="marketplace", display_name="Google Business Profile",
        auth_type="oauth2", required_secret_slots=["client_secret"], required_config_keys=["clientId"],
        capability_codes=["business_profile"], supports_sandbox=False,
    )
    db_session.add(provider)
    db_session.flush()
    connection = IntegrationConnection(
        provider_id=provider.id, scope=ConnectionScope.PLATFORM, tenant_id=None, display_name="Nexotec GBP",
        environment=ConnectionEnvironment.PRODUCTION, config={"clientId": CLIENT_ID}, enabled=True,
    )
    db_session.add(connection)
    db_session.commit()
    secrets_store.values[(connection.id, "client_secret")] = CLIENT_SECRET
    return connection


def _location(db_session, dealership_id, name="Zürich") -> Location:
    location = Location(tenant_id=dealership_id, name=name, address_locality="Zürich", location_types=[])
    db_session.add(location)
    db_session.commit()
    return location


@pytest.fixture
def betrieb(db_session):
    dealership = _make_dealership(db_session)
    manager = make_user(db_session, tenant_id=dealership.id, email=f"chef-{uuid.uuid4().hex[:6]}@example.test")
    return dealership, manager, _location(db_session, dealership.id)


def _as(user, *, manager: bool = True, tenant_id=None) -> dict[str, str]:
    return bearer(token(tenant_id=tenant_id or user.tenant_id, user_id=user.id, is_dealer_manager=manager))


def _base(dealership_id) -> str:
    return f"/v1/dealerships/{dealership_id}/business-profile"


def _start(client, dealership, manager, location) -> str:
    response = client.post(f"{_base(dealership.id)}/links", json={"locationId": str(location.id)}, headers=_as(manager))
    assert response.status_code == 201, response.text
    return response.json()["authorizeUrl"]


def _link(client, fake, dealership, manager, location, **sign_in) -> dict:
    callback = fake.sign_in(_start(client, dealership, manager, location), **sign_in)
    response = client.post(CALLBACK, json=callback, headers=_as(manager))
    assert response.status_code == 200, response.text
    return response.json()


def _pick(client, dealership, manager, binding: dict, *, account=ACCOUNT_1, location=LOCATION_1):
    return client.put(
        f"{_base(dealership.id)}/bindings/{binding['id']}/google-location",
        json={"accountName": account, "locationName": location},
        headers={**_as(manager), "If-Match": str(binding["version"])},
    )


# -- the link ------------------------------------------------------------------------------


def test_the_consent_url_asks_offline_business_manage_with_pkce_and_stores_a_hash_only(
    client, db_session, google, client_connection, betrieb
):
    dealership, manager, location = betrieb
    query = gbp_fakes.query_of(_start(client, dealership, manager, location))

    assert query["scope"] == gbp.SCOPE and query["access_type"] == "offline" and query["prompt"] == "consent"
    assert query["code_challenge"] == gbp_link.code_challenge(query["state"])
    assert query["redirect_uri"].endswith(gbp_link.CALLBACK_PATH)
    row = db_session.scalar(select(GbpLinkState))
    assert row.location_id == location.id and row.user_id == manager.id
    stored = " ".join(str(getattr(row, c.key)) for c in GbpLinkState.__table__.columns)
    for secret in (query["state"], gbp_link.code_verifier(query["state"])):
        assert secret not in stored


def test_a_location_is_linked_picked_and_its_grant_kept_in_the_secrets_manager_only(
    client, db_session, google, client_connection, betrieb
):
    fake, secrets_store = google
    dealership, manager, location = betrieb

    outcome = _link(client, fake, dealership, manager, location)

    # Both pages of accounts, every location of each (the read mask sent).
    assert [c["locationName"] for c in outcome["choices"]] == [LOCATION_1, LOCATION_2, LOCATION_3]
    assert all(r.url.params["readMask"] == "name,title,storefrontAddress" for r in fake.calls("/locations"))
    assert len(fake.calls(gbp.ACCOUNTS_URL)) == 2
    binding = outcome["binding"]
    assert binding["status"] == "pending" and binding["locationLabel"] == "Zürich"
    row = db_session.get(GbpLocationBinding, uuid.UUID(binding["id"]))
    refresh = secrets_store.values[(row.id, "refresh_token")]
    assert refresh in fake.refresh_tokens
    assert refresh not in " ".join(str(getattr(row, c.key)) for c in GbpLocationBinding.__table__.columns)
    assert "refresh" not in str(outcome).lower().replace("labelrefreshed", "")

    picked = _pick(client, dealership, manager, binding)

    assert picked.status_code == 200, picked.text
    assert picked.json()["status"] == "active"
    assert (picked.json()["googleAccountName"], picked.json()["googleLocationName"]) == (ACCOUNT_1, LOCATION_1)
    assert picked.json()["googleLocationTitle"] == "Garage Beispiel Zürich"
    listed = client.get(f"{_base(dealership.id)}/bindings", headers=_as(manager)).json()
    assert listed["configured"] is True and [b["id"] for b in listed["items"]] == [binding["id"]]
    # One call-log row per request, on Nexotec's client connection.
    operations = list(
        db_session.scalars(
            select(IntegrationCallLog.capability)
            .where(IntegrationCallLog.connection_id == client_connection.id)
            .order_by(IntegrationCallLog.created_at)
        )
    )
    assert operations.count("oauth2_token") == 1 and operations.count("accounts_list") == 2
    assert operations.count("oauth2_token_refresh") == 1  # the pick, under the binding's own grant
    actions = set(db_session.scalars(select(AuditEvent.action).where(AuditEvent.entity_id == row.id)))
    assert {"business_profile_linked", "business_profile_location_bound"} <= actions


def test_a_state_is_single_use_bound_to_its_user_and_expires(client, db_session, google, client_connection, betrieb):
    fake, _ = google
    dealership, manager, location = betrieb
    colleague = make_user(db_session, tenant_id=dealership.id, email="kollege@example.test")
    callback = fake.sign_in(_start(client, dealership, manager, location))

    refused = client.post(CALLBACK, json=callback, headers=_as(colleague))
    assert refused.status_code == 409 and refused.json()["error"]["details"]["reason"] == "link_expired"

    first = client.post(CALLBACK, json=callback, headers=_as(manager))
    assert first.status_code == 200, first.text
    again = client.post(CALLBACK, json=callback, headers=_as(manager))
    assert again.status_code == 409 and again.json()["error"]["details"]["reason"] == "link_expired"

    # An expired state is the same refusal.
    disconnect = _disconnect(client, dealership, manager, first.json()["binding"])
    assert disconnect.status_code == 204
    late = fake.sign_in(_start(client, dealership, manager, location))
    db_session.execute(
        GbpLinkState.__table__.update()
        .where(GbpLinkState.consumed_at.is_(None))
        .values(expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1))
    )
    db_session.commit()
    expired = client.post(CALLBACK, json=late, headers=_as(manager))
    assert expired.status_code == 409 and expired.json()["error"]["details"]["reason"] == "link_expired"


def test_a_grant_without_offline_access_or_the_scope_is_refused(client, db_session, google, client_connection, betrieb):
    fake, _ = google
    dealership, manager, location = betrieb
    for sign_in in ({"issue_refresh_token": False}, {"scope": "openid email"}):
        callback = fake.sign_in(_start(client, dealership, manager, location), **sign_in)
        response = client.post(CALLBACK, json=callback, headers=_as(manager))
        assert response.status_code == 409
        assert response.json()["error"]["details"]["reason"] == "business_profile_consent_incomplete"
    assert db_session.scalar(select(GbpLocationBinding)) is None


def _issued_refresh_tokens(fake) -> set[str]:
    return set(fake.refresh_tokens)


def test_a_refused_link_revokes_the_refresh_token_google_issued(
    client, db_session, google, client_connection, betrieb
):
    fake, secrets_store = google
    dealership, manager, location = betrieb

    # The scope missing: the token Google issued is ended at once.
    callback = fake.sign_in(_start(client, dealership, manager, location), scope="openid email")
    response = client.post(CALLBACK, json=callback, headers=_as(manager))
    assert response.json()["error"]["details"]["reason"] == "business_profile_consent_incomplete"
    assert fake.revoked == sorted(_issued_refresh_tokens(fake))  # the one token, revoked
    # The listing refused: the same.
    fake.refuse_listing = True
    callback = fake.sign_in(_start(client, dealership, manager, location))
    response = client.post(CALLBACK, json=callback, headers=_as(manager))
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "business_profile_consent_incomplete"
    assert set(fake.revoked) == _issued_refresh_tokens(fake) and len(fake.revoked) == 2
    # One call-log row per revoke; nothing stored.
    revokes = db_session.scalars(
        select(IntegrationCallLog).where(IntegrationCallLog.capability == "oauth2_revoke")
    ).all()
    assert len(revokes) == 2
    assert db_session.scalar(select(GbpLocationBinding)) is None and not secrets_store.values.keys() - {
        (client_connection.id, "client_secret")
    }


def test_a_failed_revoke_never_changes_the_refusal_and_never_logs_the_token(
    client, db_session, google, client_connection, betrieb, caplog
):
    fake, _ = google
    dealership, manager, location = betrieb
    fake.fail_revoke_with = 503
    callback = fake.sign_in(_start(client, dealership, manager, location), scope="openid email")

    with caplog.at_level("DEBUG"):
        response = client.post(CALLBACK, json=callback, headers=_as(manager))

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "business_profile_consent_incomplete"
    (token,) = _issued_refresh_tokens(fake)
    assert "could not be revoked" in caplog.text and token not in caplog.text
    assert token not in response.text
    failed = db_session.scalar(select(IntegrationCallLog).where(IntegrationCallLog.capability == "oauth2_revoke"))
    assert failed.http_status == 503 and token not in str(failed.provider_error)


def test_only_the_manager_links_and_nothing_starts_before_nexotec_set_its_client_up(
    client, db_session, google, betrieb
):
    dealership, manager, location = betrieb
    colleague = make_user(db_session, tenant_id=dealership.id, email="verkauf@example.test")
    body = {"locationId": str(location.id)}

    not_manager = client.post(f"{_base(dealership.id)}/links", json=body, headers=_as(colleague, manager=False))
    assert not_manager.status_code == 403
    unconfigured = client.post(f"{_base(dealership.id)}/links", json=body, headers=_as(manager))
    assert unconfigured.status_code == 409
    assert unconfigured.json()["error"]["details"]["reason"] == "business_profile_not_configured"


# -- tenancy and duplicates ---------------------------------------------------------------------


def test_another_betriebs_location_and_binding_are_never_reached(client, db_session, google, client_connection, betrieb):
    fake, _ = google
    dealership, manager, location = betrieb
    binding = _link(client, fake, dealership, manager, location)["binding"]
    other = _make_dealership(db_session)
    intruder = make_user(db_session, tenant_id=other.id, email="fremd@example.test")
    headers = {**_as(intruder), "If-Match": str(binding["version"])}

    # Their own path, our binding id: 404, never 403.
    path = f"{_base(other.id)}/bindings/{binding['id']}"
    assert client.get(f"{path}/google-locations", headers=headers).status_code == 404
    assert client.put(f"{path}/google-location", json={"accountName": ACCOUNT_1, "locationName": LOCATION_1},
                      headers=headers).status_code == 404
    assert client.delete(path, headers=headers).status_code == 404
    # Our path with their token: 404 too.
    assert client.get(f"{_base(dealership.id)}/bindings", headers=_as(intruder)).status_code == 404
    # Our location from their Betrieb is not one of theirs.
    started = client.post(f"{_base(other.id)}/links", json={"locationId": str(location.id)}, headers=_as(intruder))
    assert started.status_code == 422 and started.json()["error"]["details"]["reason"] == "unknown_location"
    assert db_session.get(GbpLocationBinding, uuid.UUID(binding["id"])).status == GbpBindingStatus.PENDING


def test_one_live_binding_per_nexotec_location_and_per_google_location(
    client, db_session, google, client_connection, betrieb
):
    fake, secrets_store = google
    dealership, manager, location = betrieb
    first = _link(client, fake, dealership, manager, location)["binding"]
    # A second sign-in before the pick replaces the pending binding (and drops its grant).
    second = _link(client, fake, dealership, manager, location)["binding"]
    assert db_session.get(GbpLocationBinding, uuid.UUID(first["id"])).status == GbpBindingStatus.DISCONNECTED
    assert (uuid.UUID(first["id"]), "refresh_token") not in secrets_store.values
    assert _pick(client, dealership, manager, second).status_code == 200

    started = client.post(f"{_base(dealership.id)}/links", json={"locationId": str(location.id)}, headers=_as(manager))
    assert started.status_code == 409 and started.json()["error"]["details"]["reason"] == "location_already_bound"

    # Another Betrieb's location may not take the Google location already bound — and is not told whose it is.
    other = _make_dealership(db_session)
    other_manager = make_user(db_session, tenant_id=other.id, email="andere@example.test")
    theirs = _link(client, fake, other, other_manager, _location(db_session, other.id, "Bern"))["binding"]
    taken = _pick(client, other, other_manager, theirs)
    assert taken.status_code == 409
    assert taken.json()["error"]["details"] == {"reason": "google_location_already_bound"}
    assert _pick(client, other, other_manager, theirs, account=ACCOUNT_2, location=LOCATION_3).status_code == 200


def test_a_location_google_does_not_list_for_the_grant_is_refused(client, db_session, google, client_connection, betrieb):
    fake, _ = google
    dealership, manager, location = betrieb
    binding = _link(client, fake, dealership, manager, location)["binding"]

    wrong_account = _pick(client, dealership, manager, binding, account=ACCOUNT_2, location=LOCATION_1)
    unknown_account = _pick(client, dealership, manager, binding, account="accounts/1100000000000000099")
    for response in (wrong_account, unknown_account):
        assert response.status_code == 409
        assert response.json()["error"]["details"]["reason"] == "google_location_not_accessible"
    malformed = _pick(client, dealership, manager, binding, location="locations/../../accounts")
    assert malformed.status_code == 422


# -- a refused grant, disconnect ------------------------------------------------------------------


def test_invalid_grant_sends_the_binding_to_error_and_never_charges_the_breaker(
    client, db_session, google, client_connection, betrieb
):
    fake, secrets_store = google
    dealership, manager, location = betrieb
    binding = _link(client, fake, dealership, manager, location)["binding"]
    fake.refused_refresh_tokens.add(secrets_store.values[(uuid.UUID(binding["id"]), "refresh_token")])

    response = client.get(f"{_base(dealership.id)}/bindings/{binding['id']}/google-locations", headers=_as(manager))

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "business_profile_grant_refused"
    row = db_session.get(GbpLocationBinding, uuid.UUID(binding["id"]))
    db_session.refresh(row)
    assert (row.status, row.error_code) == (GbpBindingStatus.ERROR, "grant_refused")
    # Past the breaker's threshold, were a refused grant charged to Nexotec's one client.
    for _ in range(resilience._FAILURE_THRESHOLD + 1):
        with (
            pytest.raises(ProviderAccountRefusedError),
            call_capability(db_session, connection=client_connection, capability=gbp.GBP_CAPABILITY) as adapter,
        ):
            adapter.access_token(binding_id=row.id)  # type: ignore[attr-defined]
    assert not resilience.is_circuit_open(client_connection.id)
    refused = db_session.scalar(
        select(IntegrationCallLog).where(IntegrationCallLog.capability == "oauth2_token_refresh")
    )
    assert (refused.http_status, refused.provider_error) == (400, "invalid_grant")


def _disconnect(client, dealership, manager, binding):
    return client.delete(
        f"{_base(dealership.id)}/bindings/{binding['id']}",
        headers={**_as(manager), "If-Match": str(binding["version"])},
    )


def test_disconnect_revokes_at_google_drops_the_secret_and_keeps_the_row(
    client, db_session, google, client_connection, betrieb
):
    fake, secrets_store = google
    dealership, manager, location = betrieb
    binding = _pick(client, dealership, manager, _link(client, fake, dealership, manager, location)["binding"]).json()
    binding_id = uuid.UUID(binding["id"])
    refresh = secrets_store.values[(binding_id, "refresh_token")]

    fake.fail_revoke_with = 503
    unreachable = _disconnect(client, dealership, manager, binding)
    assert unreachable.status_code == 422 and unreachable.json()["error"]["details"]["reason"] == "provider_unavailable"
    assert db_session.get(GbpLocationBinding, binding_id).status == GbpBindingStatus.ACTIVE
    assert (binding_id, "refresh_token") in secrets_store.values

    fake.fail_revoke_with = None
    assert _disconnect(client, dealership, manager, binding).status_code == 204
    assert fake.revoked == [refresh]
    assert (binding_id, "refresh_token") not in secrets_store.values
    row = db_session.get(GbpLocationBinding, binding_id)
    db_session.refresh(row)
    assert row.status == GbpBindingStatus.DISCONNECTED and row.disconnected_at is not None
    again = _disconnect(client, dealership, manager, {"id": binding["id"], "version": row.version})
    assert again.status_code == 409 and again.json()["error"]["details"]["reason"] == "binding_already_disconnected"
    # The location and the Google location are free again.
    assert _pick(client, dealership, manager, _link(client, fake, dealership, manager, location)["binding"]).status_code == 200


def test_a_betrieb_cannot_hold_a_connection_of_its_own_to_the_provider(client, db_session, client_connection, betrieb):
    _, manager, _ = betrieb
    response = client.post(
        "/v1/integrations/connections",
        json={
            "providerId": str(client_connection.provider_id), "displayName": "Mein Google",
            "environment": "production", "config": {"clientId": "x"},
        },
        headers=_as(manager),
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "business_profile_platform_only"


# -- the adapter: refresh, PATCH and its mask, photos ---------------------------------------------


def _adapter(db_session, client_connection) -> gbp.GoogleBusinessProfileAdapter:
    return gbp.GoogleBusinessProfileAdapter(db_session, client_connection, None, "test")


def _bound_binding(client, google, dealership, manager, location) -> uuid.UUID:
    fake, _ = google
    binding = _pick(client, dealership, manager, _link(client, fake, dealership, manager, location)["binding"]).json()
    return uuid.UUID(binding["id"])


def test_a_refresh_token_google_rotates_replaces_the_stored_one(client, db_session, google, client_connection, betrieb):
    fake, secrets_store = google
    binding_id = _bound_binding(client, google, *betrieb)
    fake.rotate_refresh_token = "1//invented-rotated-refresh"
    adapter = _adapter(db_session, client_connection)

    token_1 = adapter.access_token(binding_id=binding_id)
    assert adapter.access_token(binding_id=binding_id) == token_1  # one refresh per adapter
    assert secrets_store.values[(binding_id, "refresh_token")] == "1//invented-rotated-refresh"
    assert len([r for r in fake.calls(gbp.TOKEN_URL) if b"refresh_token" in r.content]) == 2  # the pick's + this one


def test_patch_sends_only_whitelisted_mask_paths(client, db_session, google, client_connection, betrieb):
    fake, _ = google
    binding_id = _bound_binding(client, google, *betrieb)
    adapter = _adapter(db_session, client_connection)
    access = adapter.access_token(binding_id=binding_id)
    sent_before = len(fake.requests)

    for mask in (("title",), ("websiteUri", "categories"), ()):
        with pytest.raises(gbp.GbpMaskRefused):
            adapter.patch_location(access_token=access, location_name=LOCATION_1, fields={}, update_mask=mask)
    assert len(fake.requests) == sent_before  # refused before anything was sent

    adapter.patch_location(
        access_token=access, location_name=LOCATION_1,
        fields={"websiteUri": "https://garage-beispiel.invalid/neu", "phoneNumbers": {"primaryPhone": "+41 44 000 00 00"}},
        update_mask=("websiteUri", "phoneNumbers.primaryPhone", "websiteUri"), validate_only=True,
    )
    patch = fake.requests[-1]
    assert patch.method == "PATCH" and str(patch.url).startswith(f"{gbp.INFORMATION_API}/{LOCATION_1}?")
    assert patch.url.params["updateMask"] == "websiteUri,phoneNumbers.primaryPhone"
    assert patch.url.params["validateOnly"] == "true"
    location = adapter.get_location(access_token=access, location_name=LOCATION_1, read_mask=("name", "websiteUri"))
    assert location.name == LOCATION_1 and fake.requests[-1].url.params["readMask"] == "name,websiteUri"


def test_a_photo_is_uploaded_in_three_steps_never_by_url(client, db_session, google, client_connection, betrieb):
    fake, _ = google
    binding_id = _bound_binding(client, google, *betrieb)
    adapter = _adapter(db_session, client_connection)
    access = adapter.access_token(binding_id=binding_id)
    sent_before = len(fake.requests)
    photo = b"\xff\xd8\xff\xe0invented-jpeg-bytes"

    media = adapter.upload_photo(
        access_token=access, account_name=ACCOUNT_1, location_name=LOCATION_1, content=photo,
        content_type="image/jpeg", category="EXTERIOR",
    )

    start, upload, create = fake.requests[sent_before:]
    parent = f"{gbp.MEDIA_API}/{ACCOUNT_1}/{LOCATION_1}/media"
    assert str(start.url) == f"{parent}:startUpload" and start.method == "POST"
    reference = gbp_fakes.RECORDED["media_start_upload"]["resourceName"]
    assert str(upload.url).split("?")[0] == f"{gbp.UPLOAD_API}/{reference}"
    assert upload.url.params["uploadType"] == "media" and upload.content == photo
    assert upload.headers["content-type"] == "image/jpeg"
    assert str(create.url) == parent
    body = json.loads(create.content)
    assert body == {"mediaFormat": "PHOTO", "locationAssociation": {"category": "EXTERIOR"},
                    "dataRef": {"resourceName": reference}}
    assert "sourceUrl" not in create.content.decode()
    assert media.name == gbp_fakes.RECORDED["media_create"]["name"]
    for bad in ({"category": "SELFIE"}, {"content_type": "image/gif"}):
        with pytest.raises(ProviderRequestError):
            adapter.upload_photo(
                access_token=access, account_name=ACCOUNT_1, location_name=LOCATION_1, content=photo,
                **{"content_type": "image/jpeg", "category": "EXTERIOR", **bad},
            )


# -- retention, labels, the catalogue row ---------------------------------------------------------


def test_spent_link_states_are_purged_after_a_day_and_live_ones_kept(db_session, betrieb):
    dealership, manager, location = betrieb
    now = dt.datetime.now(dt.UTC)

    def _state(**times):
        row = GbpLinkState(
            state_hash=uuid.uuid4().hex + uuid.uuid4().hex, tenant_id=dealership.id, user_id=manager.id,
            location_id=location.id, **times,
        )
        db_session.add(row)
        return row

    live = _state(expires_at=now + dt.timedelta(minutes=5))
    _state(expires_at=now - dt.timedelta(days=2))
    _state(expires_at=now + dt.timedelta(minutes=5), consumed_at=now - dt.timedelta(days=2))
    recent = _state(expires_at=now - dt.timedelta(hours=1))
    db_session.commit()

    assert gbp_link.purge_spent_link_states(db_session) == 2
    assert set(db_session.scalars(select(GbpLinkState.id))) == {live.id, recent.id}


def test_a_renamed_location_is_refreshed_on_its_binding(client, db_session, google, client_connection, betrieb):
    dealership, manager, location = betrieb
    binding_id = _bound_binding(client, google, dealership, manager, location)
    location.name = "Zürich-Altstetten"
    db_session.commit()

    assert refresh_location_labels(db_session) == 1
    row = db_session.get(GbpLocationBinding, binding_id)
    db_session.refresh(row)
    assert row.location_label == "Zürich-Altstetten"
    assert oldest_location_label_age_seconds(db_session) is not None


def test_the_migration_seeds_the_provider_the_code_registers():
    (path,) = _MIGRATION.glob("b2be6d0c6a85_*.py")
    spec = importlib.util.spec_from_file_location("kan226_gbp_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    row = module.PROVIDER_ROW
    assert row["provider_code"] == GBP_PROVIDER_CODE == gbp.GoogleBusinessProfileAdapter.provider_code
    assert (row["category"], row["required_secret_slots"], row["required_config_keys"]) == (
        "marketplace", ["client_secret"], ["clientId"]
    )
    assert row["supports_sandbox"] is False


# -- offboarding ----------------------------------------------------------------------------------


def _offboard(db_session, dealership):
    from app.core.outbox_model import OutboxMessage
    from app.platform.administration.services import offboarding

    offboarding.publish_offboarded(db_session, dealership=dealership, offboarded_at=offboarding.utcnow())
    db_session.commit()
    return db_session.scalar(
        select(OutboxMessage).where(
            OutboxMessage.event_type == offboarding.OFFBOARDED_EVENT_TYPE, OutboxMessage.tenant_id == dealership.id
        )
    )


def test_offboarding_ends_every_live_binding_once(client, db_session, google, client_connection, betrieb):
    from app.integration.consumers import handle_dealership_offboarded_message

    fake, secrets_store = google
    dealership, manager, location = betrieb
    active = _bound_binding(client, google, dealership, manager, location)
    pending = uuid.UUID(
        _link(client, fake, dealership, manager, _location(db_session, dealership.id, "Winterthur"))["binding"]["id"]
    )
    other = _make_dealership(db_session)
    other_manager = make_user(db_session, tenant_id=other.id, email="nachbar@example.test")
    theirs = _link(client, fake, other, other_manager, _location(db_session, other.id, "Bern"))["binding"]
    picked = _pick(client, other, other_manager, theirs, account=ACCOUNT_2, location=LOCATION_3)
    untouched = uuid.UUID(picked.json()["id"])
    tokens = {b: secrets_store.values[(b, "refresh_token")] for b in (active, pending)}
    message = _offboard(db_session, dealership)

    fake.fail_revoke_with = 503  # Google down: each binding ends all the same
    handle_dealership_offboarded_message(db_session, message)
    db_session.commit()

    for binding_id in (active, pending):
        row = db_session.get(GbpLocationBinding, binding_id)
        db_session.refresh(row)
        assert row.status == GbpBindingStatus.DISCONNECTED and row.disconnected_at is not None
        assert (binding_id, "refresh_token") not in secrets_store.values
    assert db_session.get(GbpLocationBinding, untouched).status == GbpBindingStatus.ACTIVE
    audits = db_session.scalars(
        select(AuditEvent).where(AuditEvent.reason == "dealership_offboarded", AuditEvent.actor_id.is_(None))
    ).all()
    assert {a.entity_id for a in audits} == {active, pending}
    assert not set(tokens.values()) & set(fake.revoked)

    # Redelivery: nothing live, nothing sent, nothing written.
    fake.fail_revoke_with = None
    sent = len(fake.requests)
    handle_dealership_offboarded_message(db_session, message)
    db_session.commit()
    assert len(fake.requests) == sent
    assert len(db_session.scalars(select(AuditEvent).where(AuditEvent.reason == "dealership_offboarded")).all()) == 2


def test_offboarding_revokes_each_grant_at_google(client, db_session, google, client_connection, betrieb):
    from app.integration.consumers import handle_dealership_offboarded_message

    fake, secrets_store = google
    dealership, manager, location = betrieb
    binding_id = _bound_binding(client, google, dealership, manager, location)
    token_value = secrets_store.values[(binding_id, "refresh_token")]

    handle_dealership_offboarded_message(db_session, _offboard(db_session, dealership))
    db_session.commit()

    assert fake.revoked == [token_value]
    row = db_session.get(GbpLocationBinding, binding_id)
    db_session.refresh(row)
    assert row.status == GbpBindingStatus.DISCONNECTED
