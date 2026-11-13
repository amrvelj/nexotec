"""KAN-588 — the catalogue administration shows platform staff, per provider
and language, when the captions were last fetched, and «Captions
aktualisieren» fetches them again through the acting Betrieb's connection
under the staff user's own token (auto-i's User Enrollment Grant names that
user), one `caption_code` request per language, audited.

The identAPI adapter on its fake transport (tests/identapi_support.FakeAutoi);
nothing here reaches the real provider.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.integration.adapters.auto_i_dat_ident import OP_CAPTION_CODE
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.caption import IntegrationProviderCaptionFetch
from app.integration.services import gateway
from app.integration.services.captions import store_captions
from tests import identapi_support as fx
from tests.test_integration_provider_captions import (  # noqa: F401 — _fresh_adapter_state is an autouse fixture
    _fresh_adapter_state,
    _LanguageAwareFake,
)


def _headers(*, user_id: uuid.UUID, tenant_id: uuid.UUID, staff: bool = True) -> dict[str, str]:
    token = create_access_token(
        user_id=user_id, tenant_id=tenant_id, group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.PLATFORM_ADMIN} if staff else {AccessRole.SALES}), is_dealer_manager=not staff,
    )
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}


def _staff_in(db_session, tenant_id: uuid.UUID):
    staff = fx.make_user(db_session, tenant_id=tenant_id, email="staff@nexotec.example", first_name="Sam", last_name="Staff")
    staff.access_roles = [AccessRole.PLATFORM_ADMIN.value]
    db_session.commit()
    return staff


def test_every_caption_store_records_when_that_language_was_fetched(db_session):
    from app.integration.adapters.base import CaptionData

    at = dt.datetime(2026, 11, 13, 9, 0, tzinfo=dt.UTC)
    rows = [CaptionData(group="fuel", kind="passenger", code="3", label="Diesel", label_short="D")]
    store_captions(db_session, provider_code="auto_i_dat", lang="de", captions=rows, now=at)
    store_captions(db_session, provider_code="auto_i_dat", lang="fr", captions=rows, now=at + dt.timedelta(seconds=2))
    store_captions(db_session, provider_code="auto_i_dat", lang="de", captions=rows + rows, now=at + dt.timedelta(days=1))
    db_session.commit()

    fetches = {
        f.lang: (f.fetched_at, f.caption_count)
        for f in db_session.scalars(select(IntegrationProviderCaptionFetch).where(IntegrationProviderCaptionFetch.provider == "auto_i_dat"))
    }
    assert fetches == {"de": (at + dt.timedelta(days=1), 1), "fr": (at + dt.timedelta(seconds=2), 1)}


def test_staff_see_per_provider_and_language_when_the_captions_were_fetched(client, db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=_LanguageAwareFake())
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)
    staff = _staff_in(db_session, s.connection.tenant_id)

    response = client.get("/v1/integrations/captions/fetches", headers=_headers(user_id=staff.id, tenant_id=s.connection.tenant_id))

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert {i["providerName"] for i in items} == {"auto-i-dat (identAPI)"}
    assert [(i["provider"], i["language"]) for i in items] == [("auto_i_dat", lang) for lang in ("de", "fr", "it", "en")]
    assert all(i["captionCount"] == 5 and i["fetchedAt"] for i in items)


def test_the_refresh_fetches_the_four_languages_under_the_staff_users_own_token(client, db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=_LanguageAwareFake())
    tenant_id = s.connection.tenant_id
    staff = _staff_in(db_session, tenant_id)

    response = client.post("/v1/integrations/captions/refresh", headers=_headers(user_id=staff.id, tenant_id=tenant_id))

    assert response.status_code == 200, response.text
    assert [i["language"] for i in response.json()["items"]] == ["de", "fr", "it", "en"]
    # the enrolment named the staff user, nobody else
    assert {r.form.get("user_id") for r in s.fake.token_requests()} == {str(staff.id)}
    assert [next(q for q in r.query if q[0] == "lang")[1] for r in s.fake.api_requests_to("/caption/code")] == ["de", "fr", "it", "en"]
    calls = db_session.scalars(
        select(IntegrationCallLog).where(IntegrationCallLog.connection_id == s.connection.id, IntegrationCallLog.capability == OP_CAPTION_CODE)
    ).all()
    assert len(calls) == 4
    (audit,) = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "captions_refreshed")).all()
    assert (audit.entity_id, audit.tenant_id, audit.actor_id) == (s.connection.id, tenant_id, staff.id)
    assert audit.after == {"provider": "auto_i_dat", "languages": ["de", "fr", "it", "en"], "complete": True}


def test_a_provider_failure_keeps_what_landed_and_says_so(client, db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=_LanguageAwareFake())
    s.fake.api_status["/caption/code"] = 503
    staff = _staff_in(db_session, s.connection.tenant_id)

    response = client.post("/v1/integrations/captions/refresh", headers=_headers(user_id=staff.id, tenant_id=s.connection.tenant_id))

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"reason": "provider_unavailable", "languagesFetched": []}
    (audit,) = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "captions_refreshed")).all()
    assert audit.after["complete"] is False


def test_a_betrieb_without_a_vehicle_data_connection_is_refused(client, db_session):
    tenant_id = uuid.uuid4()
    staff = _staff_in(db_session, tenant_id)

    response = client.post("/v1/integrations/captions/refresh", headers=_headers(user_id=staff.id, tenant_id=tenant_id))

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "no_provider_connection"


@pytest.mark.parametrize(("method", "path"), [("GET", "/v1/integrations/captions/fetches"), ("POST", "/v1/integrations/captions/refresh")])
def test_both_routes_are_platform_staffs_only(client, method, path):
    response = client.request(method, path, headers=_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), staff=False))

    assert response.status_code == 403, response.text


def test_a_connection_without_its_secret_is_the_dealers_to_fix_not_unavailable(client, db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=_LanguageAwareFake())
    s.secrets.values.clear()  # the client secret is gone: a configuration error, not an outage
    staff = _staff_in(db_session, s.connection.tenant_id)

    response = client.post("/v1/integrations/captions/refresh", headers=_headers(user_id=staff.id, tenant_id=s.connection.tenant_id))

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {"reason": "provider_not_configured", "languagesFetched": []}


def test_a_provider_without_a_vocabulary_is_refused_and_still_audited(client, db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=_LanguageAwareFake())
    staff = _staff_in(db_session, s.connection.tenant_id)
    from app.integration.adapters.auto_i_dat_ident import AutoIDatIdentAdapter

    monkeypatch.delattr(AutoIDatIdentAdapter, "fetch_captions")

    response = client.post("/v1/integrations/captions/refresh", headers=_headers(user_id=staff.id, tenant_id=s.connection.tenant_id))

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "provider_has_no_captions"
    (audit,) = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "captions_refreshed")).all()
    assert audit.after == {"provider": "auto_i_dat", "languages": [], "complete": False}
    # Nothing left for the provider: no failed call logged, the breaker not charged.
    calls = db_session.scalars(select(IntegrationCallLog).where(IntegrationCallLog.connection_id == s.connection.id)).all()
    assert [c for c in calls if c.capability == OP_CAPTION_CODE] == []
    for _ in range(5):
        again = client.post("/v1/integrations/captions/refresh", headers=_headers(user_id=staff.id, tenant_id=s.connection.tenant_id))
        assert again.json()["error"]["details"]["reason"] == "provider_has_no_captions"
