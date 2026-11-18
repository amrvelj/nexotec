"""KAN-488 — the identAPI foundation: OAuth 2.0 (discovery + User Enrollment
Grant), the httpx transport, Test connection with the six option probes.

Everything runs the real, registered `auto_i_dat` adapter against a fake
authorization server and identAPI over `httpx.MockTransport`
(`tests/identapi_support.py`); no network, no real value. The exit criteria
the ticket names are each a test here: a wrong secret ends as `error` with
no API call (2); the token is reused across calls, refreshed once on 401 and
invalidated by a rotation (3); no secret or token reaches a log line (4);
every request goes through `_request` (architecture test, 5).
"""

import json
import logging
import uuid

import pytest

from app.core.auth import create_access_token
from app.core.observability import JSONLogFormatter
from app.core.redact import SECRET_FIELDS
from app.integration.adapters import auto_i_dat_ident, autoi_oauth
from app.integration.adapters.auto_i_dat_ident import OP_CAPTION_CODE
from app.integration.adapters.autoi_oauth import OAUTH_DISCOVERY_OPERATION, OAUTH_TOKEN_OPERATION
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.connection import ConnectionStatus
from app.integration.models.entitlement import EntitlementSource
from app.integration.models.secret_ref import SecretSlot
from app.integration.services import connections as connection_service
from app.integration.services import gateway, resilience
from tests import identapi_support as fx
from tests.identapi_support import PROBE_OPERATIONS, PROBE_PATHS, FakeAutoi
from tests.token_support import dealer_group_of


@pytest.fixture(autouse=True)
def _fresh_adapter_state():
    yield
    fx.uninstall()
    for connection_id in list(resilience._CIRCUITS):
        resilience.reset_circuit(connection_id)


def _rows(db_session, connection, operation: str | None = None) -> list[IntegrationCallLog]:
    query = db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id)
    if operation is not None:
        query = query.filter_by(capability=operation)
    return query.order_by(IntegrationCallLog.created_at, IntegrationCallLog.id).all()


def _entitlements(db_session, connection) -> dict[str, bool]:
    return {
        row.capability_code: row.granted
        for row in connection_service.list_entitlements(db_session, connection_id=connection.id)
    }


# --- the registered adapter ------------------------------------------------------------


def test_the_identapi_adapter_is_what_auto_i_dat_resolves_to():
    assert gateway._ADAPTER_FACTORIES["auto_i_dat"] is auto_i_dat_ident._build_ident_adapter


# --- Test connection: token, caption, six probes ----------------------------------------


def test_test_connection_logs_in_reads_the_captions_and_probes_every_option(db_session, monkeypatch):
    fake = FakeAutoi(api_status={PROBE_PATHS["vin"]: 403, PROBE_PATHS["bewertung"]: 403})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.CONNECTED
    assert updated.last_error is None
    assert updated.last_verified_at is not None

    # One login for the whole test: the token is reused by the caption call
    # and all six probes (exit criterion 3).
    token_rows = _rows(db_session, s.connection, OAUTH_TOKEN_OPERATION)
    assert [(r.status, r.cost_units) for r in token_rows] == [(CallStatus.SUCCESS, 0)]
    assert [(r.status, r.cost_units) for r in _rows(db_session, s.connection, OAUTH_DISCOVERY_OPERATION)] == [
        (CallStatus.SUCCESS, 0)
    ]
    # KAN-547: the access check's German captions, then French, Italian and
    # English — four `caption_code` rows, the vocabulary stored in four languages.
    assert [r.status for r in _rows(db_session, s.connection, OP_CAPTION_CODE)] == [CallStatus.SUCCESS] * 4
    assert len(s.fake.token_requests()) == 1

    # Every probe is one request, named by its identAPI operation (KAN-486).
    for code, operation in PROBE_OPERATIONS.items():
        rows = _rows(db_session, s.connection, operation)
        expected = CallStatus.ERROR if code in ("vin", "bewertung") else CallStatus.SUCCESS
        assert [r.status for r in rows] == [expected], (code, rows)
    assert {r.path for r in s.fake.api_requests()} == {"/caption/code", *PROBE_PATHS.values()}
    assert {r.capability for r in _rows(db_session, s.connection)} == {
        OAUTH_DISCOVERY_OPERATION, OAUTH_TOKEN_OPERATION, OP_CAPTION_CODE, *PROBE_OPERATIONS.values(),
    }

    # Six rows, probed, 403 = not granted (exit criterion 1's shape).
    rows = connection_service.list_entitlements(db_session, connection_id=s.connection.id)
    assert {r.source for r in rows} == {EntitlementSource.PROBED}
    assert _entitlements(db_session, s.connection) == {
        "ident_basis": True, "ausstattung": True, "bewertung": False, "vin": False, "pneu": True, "energielabel": True,
    }


def test_the_enrollment_grant_names_the_acting_user_and_the_scope(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    (token_request,) = s.fake.token_requests()
    assert token_request.form == {
        "grant_type": "user_enrollment",
        "scope": "ident",
        "user_id": str(s.user.id),
        "email": s.user.email,
        "first_name": s.user.first_name,
        "last_name": s.user.last_name,
    }
    assert token_request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert token_request.headers["accept-language"] == "de-CH"
    assert token_request.headers["authorization"].startswith("Basic ")
    # Discovery was read first and never hard-coded: the token went where the
    # metadata said.
    assert s.fake.seen[0].path == "/.well-known/oauth-authorization-server"
    # The API calls carry the issued Bearer token and nothing else of the login.
    for request in s.fake.api_requests():
        assert request.headers["authorization"] == f"Bearer {s.fake.tokens_issued[0]}"


def _ui_language(db_session, user, language: str) -> None:
    from app.platform.identity.models.user_preference import UserPreference

    db_session.add(UserPreference(user_id=user.id, scope="ui", payload={"uiLanguage": language}))
    db_session.commit()


def test_the_enrollment_sends_the_users_own_ui_language(db_session, monkeypatch):
    """KAN-585: a user who chose French is enrolled as `fr-CH`, not as the
    `de-CH` every user was sent as before."""

    s = fx.connected(db_session, monkeypatch)
    _ui_language(db_session, s.user, "fr")

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    (token_request,) = s.fake.token_requests()
    assert token_request.headers["accept-language"] == "fr-CH"


def test_without_a_preference_the_enrollment_sends_the_home_betriebs_language(db_session, monkeypatch):
    """KAN-585 with the KAN-104 rule: no `uiLanguage` of their own, so the
    user starts in their home Betrieb's default correspondence language."""

    from app.core.i18n import SwissLanguage
    from app.platform.organisation.models.dealership import DealerGroup, Dealership, FranchiseType
    from tests.dealership_support import valid_uid

    group = DealerGroup(name="Gruppo Ticino")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        dealer_group_id=group.id, legal_name="Garage Lugano SA", franchise_type=FranchiseType.INDEPENDENT,
        address_street="Via Nassa", address_house_number="1", address_postal_code="6900",
        address_locality="Lugano", address_canton="TI", phone="+41911234567", uid=valid_uid(),
        default_correspondence_language=SwissLanguage.IT,
    )
    db_session.add(dealership)
    db_session.commit()
    s = fx.connected(db_session, monkeypatch, tenant_id=dealership.id)

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    (token_request,) = s.fake.token_requests()
    assert token_request.headers["accept-language"] == "it-CH"


def test_a_language_change_does_not_enrol_the_user_a_second_time(db_session, monkeypatch):
    """KAN-585, do not touch: the token cache key stays (connection,
    version/rotated_at, user) — a changed language reuses the token."""

    s = fx.connected(db_session, monkeypatch)
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)
    _ui_language(db_session, s.user, "en")
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert len(s.fake.token_requests()) == 1


@pytest.mark.parametrize(
    ("ui_language", "tag"),
    [("de", "de-CH"), ("fr", "fr-CH"), ("it", "it-CH"), ("en", "en-CH"), ("FR", "fr-CH"), (None, "de-CH"), ("rm", "de-CH")],
)
def test_the_enrolment_language_tag(ui_language, tag):
    assert auto_i_dat_ident.enrolment_language_tag(ui_language) == tag


def test_the_client_secret_is_resolved_once_per_token_request_and_audited(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert s.secrets.resolutions == 1
    from app.core.audit import AuditEvent

    resolutions = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="integration_secret_ref", entity_id=s.connection.id, action="secret_resolved")
        .all()
    )
    assert len(resolutions) == 1
    assert resolutions[0].actor_id == s.user.id
    assert "slot=client_secret" in (resolutions[0].reason or "")


# --- exit criterion 2: a wrong secret ----------------------------------------------------


def test_a_wrong_client_secret_ends_as_error_naming_the_token_endpoint_and_makes_no_api_call(
    db_session, monkeypatch
):
    s = fx.connected(db_session, monkeypatch)
    s.secrets.values[(s.connection.id, "client_secret")] = "not-the-secret"

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert updated.last_verified_at is None
    assert updated.last_error is not None
    assert "token endpoint" in updated.last_error and "401" in updated.last_error
    assert "invalid_client" in updated.last_error
    assert "not-the-secret" not in updated.last_error
    assert s.fake.api_requests() == []
    # Discovery, then the one login, logged as the failure it was, both at no cost.
    assert [(r.capability, r.status, r.cost_units) for r in _rows(db_session, s.connection)] == [
        (OAUTH_DISCOVERY_OPERATION, CallStatus.SUCCESS, 0),
        (OAUTH_TOKEN_OPERATION, CallStatus.ERROR, 0),
    ]
    assert connection_service.list_entitlements(db_session, connection_id=s.connection.id) == []


def test_a_user_quota_exceeded_answer_is_named_as_such(db_session, monkeypatch):
    fake = FakeAutoi(token_status=400)
    s = fx.connected(db_session, monkeypatch, fake=fake)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert "user quota" in (updated.last_error or "")


def test_a_missing_client_secret_is_the_dealers_to_fix_not_a_provider_failure(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)
    s.secrets.values.clear()
    charged: list[uuid.UUID] = []
    monkeypatch.setattr(resilience, "record_failure", charged.append)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert "'client_secret' credential" in (updated.last_error or "")
    assert s.fake.token_requests() == []
    # Discovery ran (and keeps its honest row); no login was attempted, no
    # breaker charge (as `wsdlUrl` was), and the secret was never resolved.
    assert [(r.capability, r.status) for r in _rows(db_session, s.connection)] == [
        (OAUTH_DISCOVERY_OPERATION, CallStatus.SUCCESS)
    ]
    assert charged == []
    # The read was attempted once (after discovery) and handed out nothing.
    assert (s.secrets.attempts, s.secrets.resolutions) == (1, 0)


def test_a_missing_client_id_is_refused_before_any_request(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)
    s.connection.config = {}
    db_session.commit()

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert "clientId" in (updated.last_error or "")
    assert s.fake.seen == []


# --- exit criterion 3: token lifetime ------------------------------------------------------


def test_the_token_is_reused_across_calls_within_its_lifetime(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    # Two tests: fourteen API requests, one login.
    assert len(s.fake.api_requests()) == 20  # two tests: caption + 3 languages + 6 probes each (KAN-547)
    assert len(s.fake.token_requests()) == 1
    assert len(_rows(db_session, s.connection, OAUTH_TOKEN_OPERATION)) == 1
    assert s.secrets.resolutions == 1


def test_an_expired_token_is_requested_again(db_session, monkeypatch):
    fake = FakeAutoi(expires_in=30)  # under the 60 s safety margin: expired on arrival
    s = fx.connected(db_session, monkeypatch, fake=fake)

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    # Every one of the ten API calls had to log in first.
    assert len(s.fake.token_requests()) == 10  # one per request: the token expires on arrival


def test_a_401_from_the_api_refreshes_the_token_once_and_logs_both_attempts(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)
    s.fake.expire_tokens_once = True

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.CONNECTED
    assert len(s.fake.token_requests()) == 2
    # The refused caption request and the one that followed with a fresh token.
    assert [r.status for r in _rows(db_session, s.connection, OP_CAPTION_CODE)] == [
        *([CallStatus.SUCCESS] * 4),  # the first test: the access check, then three more languages (KAN-547)
        CallStatus.ERROR, CallStatus.SUCCESS,  # the refused caption request and its retry with a fresh token
        *([CallStatus.SUCCESS] * 3),
    ]


def test_a_second_401_is_a_rejection_not_an_endless_refresh(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)
    # The API now refuses every token, whatever the authorization server issues.
    s.fake.tokens_issued.clear()
    monkeypatch.setattr(s.fake, "tokens_issued", _NeverContains())

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert "twice" in (updated.last_error or "") and "401" in (updated.last_error or "")
    # First test's login + exactly one refresh in the second.
    assert len(s.fake.token_requests()) == 2


class _NeverContains(list):
    def __contains__(self, item) -> bool:
        return False

    def append(self, item) -> None:  # the fake still records what it issued
        super().append(item)

    def __getitem__(self, index):
        return super().__getitem__(index)


def test_rotating_the_secret_invalidates_the_cached_token(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    connection_service.set_secret(
        db_session, connection=s.connection, slot=SecretSlot.CLIENT_SECRET, value=fx.CLIENT_SECRET,
        actor_id=s.user.id, secrets_backend_module=s.secrets,
    )
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert len(s.fake.token_requests()) == 2


def test_each_user_gets_their_own_token(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)
    other = fx.make_user(db_session, tenant_id=s.connection.tenant_id, email="bob.tester@example.test", first_name="Bob")

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)
    gateway.test_connection(db_session, connection=s.connection, actor_id=other.id)

    assert [r.form["user_id"] for r in s.fake.token_requests()] == [str(s.user.id), str(other.id)]


def test_a_call_with_no_signed_in_user_is_refused_before_anything_is_sent(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=None)

    assert updated.status == ConnectionStatus.ERROR
    assert "signed-in user" in (updated.last_error or "")
    assert s.fake.seen == []


def test_a_user_who_does_not_act_in_the_betrieb_has_no_token(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)
    stranger = fx.make_user(db_session, tenant_id=uuid.uuid4(), email="eve.elsewhere@example.test", first_name="Eve")

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=stranger.id)

    assert updated.status == ConnectionStatus.ERROR
    assert "not a member of this connection's Betrieb" in (updated.last_error or "")
    assert s.fake.seen == []


# --- discovery -----------------------------------------------------------------------------


def test_discovery_without_client_secret_basic_is_a_configuration_error(db_session, monkeypatch):
    fake = FakeAutoi(discovery={**fx.DISCOVERY, "token_endpoint_auth_methods_supported": ["private_key_jwt"]})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert "client_secret_basic" in (updated.last_error or "")
    assert s.fake.token_requests() == []


def test_discovery_is_read_once_and_cached(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)
    other = fx.make_user(db_session, tenant_id=s.connection.tenant_id, email="bob.tester@example.test", first_name="Bob")

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)
    gateway.test_connection(db_session, connection=s.connection, actor_id=other.id)

    assert len([r for r in s.fake.seen if r.path == "/.well-known/oauth-authorization-server"]) == 1


def test_an_unreachable_authorization_server_is_a_transport_failure(db_session, monkeypatch):
    fake = FakeAutoi(discovery_status=503)
    s = fx.connected(db_session, monkeypatch, fake=fake)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert "HTTP 503" in (updated.last_error or "")


# --- the probes' verdicts ------------------------------------------------------------------


def test_a_422_on_a_probe_leaves_the_entitlement_row_untouched(db_session, monkeypatch):
    fake = FakeAutoi(api_status={PROBE_PATHS["pneu"]: 422})
    s = fx.connected(db_session, monkeypatch, fake=fake)
    connection_service.record_probed_entitlement(db_session, connection_id=s.connection.id, capability_code="pneu", granted=False)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.CONNECTED
    assert _entitlements(db_session, s.connection)["pneu"] is False  # the earlier verdict, not flipped
    (row,) = _rows(db_session, s.connection, PROBE_OPERATIONS["pneu"])
    assert row.status == CallStatus.ERROR


def test_a_5xx_on_a_probe_is_retried_once_then_leaves_the_row_untouched(db_session, monkeypatch):
    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))
    fake = FakeAutoi(api_status={PROBE_PATHS["energielabel"]: 503})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.CONNECTED
    assert "energielabel" not in _entitlements(db_session, s.connection)
    assert [r.status for r in _rows(db_session, s.connection, PROBE_OPERATIONS["energielabel"])] == [
        CallStatus.ERROR, CallStatus.ERROR,
    ]


def test_a_transient_5xx_recovers_on_the_retry(db_session, monkeypatch):
    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))
    fake = FakeAutoi(api_fail_once={"/caption/code": 502})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.CONNECTED
    assert [r.status for r in _rows(db_session, s.connection, OP_CAPTION_CODE)] == [
        CallStatus.ERROR, CallStatus.SUCCESS, *([CallStatus.SUCCESS] * 3)  # the retry, then three more languages
    ]


def test_a_4xx_is_never_retried(db_session, monkeypatch):
    fake = FakeAutoi(api_status={PROBE_PATHS["vin"]: 403})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert len([r for r in s.fake.api_requests() if r.path == PROBE_PATHS["vin"]]) == 1


def test_a_timeout_is_logged_as_timeout(db_session, monkeypatch):
    import httpx

    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))
    fake = FakeAutoi(raise_on_api=httpx.ReadTimeout("read timed out"))
    s = fx.connected(db_session, monkeypatch, fake=fake)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert "ReadTimeout" in (updated.last_error or "")
    assert [r.status for r in _rows(db_session, s.connection, OP_CAPTION_CODE)] == [CallStatus.TIMEOUT, CallStatus.TIMEOUT]


def test_a_refused_probe_is_never_charged_to_the_circuit_breaker(db_session, monkeypatch):
    fake = FakeAutoi(api_status={path: 403 for path in PROBE_PATHS.values()})
    s = fx.connected(db_session, monkeypatch, fake=fake)
    charged: list[uuid.UUID] = []
    monkeypatch.setattr(resilience, "record_failure", charged.append)

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert charged == []
    assert set(_entitlements(db_session, s.connection).values()) == {False}


# --- exit criterion 4: nothing secret in a log line ----------------------------------------


def test_the_token_and_the_authorization_header_are_redacted_field_names():
    assert {"access_token", "authorization", "Authorization", "client_secret"} <= SECRET_FIELDS
    record = logging.LogRecord(
        name="app.integration", level=logging.INFO, pathname=__file__, lineno=1, msg="token", args=(), exc_info=None
    )
    record.access_token = "tok-should-never-appear"  # type: ignore[attr-defined]
    record.Authorization = "Bearer tok-should-never-appear"  # type: ignore[attr-defined]
    payload = json.loads(JSONLogFormatter().format(record))
    assert payload["access_token"] == "***redacted***"
    assert payload["Authorization"] == "***redacted***"


def test_no_log_line_or_last_error_carries_the_secret_or_a_token(db_session, monkeypatch, caplog):
    s = fx.connected(db_session, monkeypatch)
    with caplog.at_level(logging.DEBUG):
        gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)
        s.fake.tokens_issued.clear()
        monkeypatch.setattr(s.fake, "tokens_issued", _NeverContains())
        updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    text = caplog.text + (updated.last_error or "")
    assert fx.CLIENT_SECRET not in text
    for token in s.fake.tokens_issued:
        assert token not in text
    assert "tok-" not in text


# --- the token cache is process memory, never a row --------------------------------------------


def test_the_token_lives_only_in_process_memory(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    (token,) = s.fake.tokens_issued
    cached = list(autoi_oauth._token_cache.values())
    assert [t.value for t in cached] == [token]
    # Nothing in the database knows it.
    db_session.expire_all()
    for table in ("integration_call_log", "integration_connection", "integration_secret_ref", "audit_event"):
        from sqlalchemy import text

        rows = db_session.execute(text(f"SELECT * FROM {table}")).mappings().all()
        assert token not in json.dumps([dict(r) for r in rows], default=str)


# --- through the HTTP endpoint: the actor is the signed-in user --------------------------------


def test_the_test_endpoint_enrols_the_signed_in_user(client, db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)
    token = create_access_token(
        user_id=s.user.id, tenant_id=s.connection.tenant_id, group_id=dealer_group_of(str(s.connection.tenant_id)),
        roles=frozenset(), is_dealer_manager=True,
    )

    response = client.post(
        f"/v1/integrations/connections/{s.connection.id}/test", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "connected"
    assert {e["capabilityCode"] for e in body["entitlements"]} == set(fx.CAPABILITY_CODES)
    (token_request,) = s.fake.token_requests()
    assert token_request.form["user_id"] == str(s.user.id)


# --- review findings (2026-11-07) ----------------------------------------------------------------


def test_a_refused_login_while_probing_leaves_every_entitlement_row_untouched(db_session, monkeypatch):
    """Finding 1: a 401 twice on a probe is a refused login, not "not licensed".
    The rows stay as they were and the connection, whose access check passed,
    stays connected."""

    import httpx

    s = fx.connected(db_session, monkeypatch)
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)
    before = _entitlements(db_session, s.connection)
    assert before == dict.fromkeys(fx.CAPABILITY_CODES, True)

    # The API now answers 401 to every probe, whatever token it carries; the
    # caption check still passes on the cached token.
    real = s.fake._ident_api

    def refuse_probes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/caption/code":
            return real(request)
        return httpx.Response(401, json={"status": "401", "title": "Unauthorized"})

    monkeypatch.setattr(s.fake, "_ident_api", refuse_probes)
    logins_before = len(s.fake.token_requests())

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.CONNECTED
    assert _entitlements(db_session, s.connection) == before
    # Probing stopped at the first refused login: one refresh, not one per
    # option; the refusal is charged once, as a refused login is anywhere,
    # never booked as a success (review F1, N4).
    assert len(s.fake.token_requests()) == logins_before + 1
    assert resilience.is_circuit_open(s.connection.id) is False
    assert resilience._circuit_for(s.connection.id).failure_count == 1


def test_the_providers_answer_to_a_bad_request_never_charges_the_breaker(db_session, monkeypatch):
    """Review F1, the other path: five 422 answers on probes (or five wrong
    Stammnummern typed by a user) are the provider answering, not failing."""

    fake = FakeAutoi(api_status={path: 422 for path in PROBE_PATHS.values()})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.CONNECTED
    assert _entitlements(db_session, s.connection) == {}
    assert resilience._circuit_for(s.connection.id).failure_count == 0
    for operation in PROBE_OPERATIONS.values():
        (row,) = _rows(db_session, s.connection, operation)
        assert row.status == CallStatus.ERROR  # refused, and logged as such


def test_a_404_on_the_access_check_is_not_a_connected_account(db_session, monkeypatch):
    """Review F5: a retired `/caption/code` path is an error, not "connected"
    with an empty vocabulary; a 404 reads as "nothing found" on lookups only."""

    fake = FakeAutoi(api_status={"/caption/code": 404})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert "HTTP 404" in (updated.last_error or "")
    assert connection_service.list_entitlements(db_session, connection_id=s.connection.id) == []


def test_last_error_carries_the_providers_title_but_never_its_prose(db_session, monkeypatch):
    """Review F6: the json:api `detail` is server prose and stays out of a
    message every dealer manager reads; the bounded `title` is enough."""

    fake = FakeAutoi(api_status={"/caption/code": 422})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert "HTTP 422" in (updated.last_error or "") and "Unprocessable" in (updated.last_error or "")
    assert "No vehicle for this key" not in (updated.last_error or "")



def test_a_failed_discovery_resolves_no_secret_and_leaves_an_honest_row(db_session, monkeypatch):
    """Findings 6 and 7: discovery is read before the secret is resolved, and
    it is a metered request of its own."""

    fake = FakeAutoi(discovery={**fx.DISCOVERY, "token_endpoint_auth_methods_supported": ["private_key_jwt"]})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert s.secrets.attempts == 0  # never even asked
    # The document arrived and was refused: that request's own ERROR row.
    assert [(r.capability, r.status, r.cost_units) for r in _rows(db_session, s.connection)] == [
        (OAUTH_DISCOVERY_OPERATION, CallStatus.ERROR, 0)
    ]


def test_every_secret_resolution_is_audited_even_when_the_login_then_fails(db_session, monkeypatch):
    """Finding 6, the other half: the audit row of a resolution survives a
    refused login (the gateway commits what the adapter recorded)."""

    from app.core.audit import AuditEvent

    s = fx.connected(db_session, monkeypatch)
    s.secrets.values[(s.connection.id, "client_secret")] = "not-the-secret"

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    resolutions = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="integration_secret_ref", entity_id=s.connection.id, action="secret_resolved")
        .count()
    )
    assert s.secrets.resolutions == 1 == resolutions


def test_removing_the_secret_drops_the_cached_token(db_session, monkeypatch):
    """Finding 8: a removed credential is a credential change — the next call
    logs in again (and fails, honestly, for want of a secret)."""

    s = fx.connected(db_session, monkeypatch)
    # The secret as a dealer sets it: a secret-ref row plus the stored value.
    connection_service.set_secret(
        db_session, connection=s.connection, slot=SecretSlot.CLIENT_SECRET, value=fx.CLIENT_SECRET,
        actor_id=s.user.id, secrets_backend_module=s.secrets,
    )
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    connection_service.remove_secret(
        db_session, connection=s.connection, slot=SecretSlot.CLIENT_SECRET, actor_id=s.user.id,
        secrets_backend_module=s.secrets,
    )
    db_session.refresh(s.connection)
    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert "'client_secret' credential" in (updated.last_error or "")
    assert len(s.fake.api_requests()) == 10  # only the first test's calls


def test_a_connection_without_a_betrieb_says_so(db_session, monkeypatch):
    """Finding 9: three reasons, three sentences."""

    from app.integration.models.connection import ConnectionScope

    s = fx.connected(db_session, monkeypatch)
    s.connection.scope = ConnectionScope.PLATFORM
    s.connection.tenant_id = None
    db_session.commit()

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert "belongs to no Betrieb" in (updated.last_error or "")


def test_a_401_invalidates_only_that_users_token(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)
    other = fx.make_user(db_session, tenant_id=s.connection.tenant_id, email="bob.tester@example.test", first_name="Bob")
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)
    gateway.test_connection(db_session, connection=s.connection, actor_id=other.id)
    assert len(s.fake.token_requests()) == 2

    s.fake.expire_tokens_once = True
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)
    assert len(s.fake.token_requests()) == 3
    gateway.test_connection(db_session, connection=s.connection, actor_id=other.id)
    assert len(s.fake.token_requests()) == 3  # Bob's token was never dropped


def test_a_404_on_a_probe_reads_as_granted(db_session, monkeypatch):
    """The rule's "200/404 = granted" (review N3): a lookup that finds nothing
    is still a lookup the licence allowed."""

    fake = FakeAutoi(api_status={PROBE_PATHS["pneu"]: 404})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.CONNECTED
    assert _entitlements(db_session, s.connection)["pneu"] is True


def test_a_403_outside_a_probe_never_charges_the_breaker(db_session, monkeypatch):
    """Review N1: a 403 is the provider saying "not this option", coherently;
    six of them in a row must not open the breaker for the options the licence
    does cover."""

    fake = FakeAutoi(api_status={"/caption/code": 403})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    for _ in range(resilience._FAILURE_THRESHOLD + 1):
        updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert "403" in (updated.last_error or "")
    assert resilience._circuit_for(s.connection.id).failure_count == 0
