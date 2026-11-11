"""KAN-586: an over-quota user never charges the connection's circuit breaker.

auto-i answers the User Enrollment Grant with `user_quota_exceeded` when the
service account has no user quota left for one more Nexotec user. Until this
ticket that answer was a plain `ProviderRejectedError`, which
`gateway._record_failed_call` charges like a wrong client secret: five users
auto-i would not enrol opened the breaker (`resilience._FAILURE_THRESHOLD`),
and every colleague who already held a token was refused «Circuit breaker is
open» for a minute.

Anto's ruling (2026-11-10, option C): the breaker fix only — what the user
reads is unchanged. These tests drive the real identAPI adapter against the
fake hosts in `tests/identapi_support.py`.
"""

import pytest

from app.integration.adapters.autoi_oauth import OAUTH_TOKEN_OPERATION
from app.integration.errors import ProviderRejectedError, ProviderUserQuotaExceededError
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.services import gateway, resilience
from tests import identapi_support as fx
from tests.identapi_support import FakeAutoi


@pytest.fixture(autouse=True)
def _reset_state():
    yield
    fx.uninstall()
    for connection_id in list(resilience._CIRCUITS):
        resilience.reset_circuit(connection_id)


def _lookup(db_session, s, *, actor_id):
    with gateway.call_capability(
        db_session, connection=s.connection, capability="ident_basis", actor_id=actor_id, purpose="test"
    ) as adapter:
        return adapter.fetch_captions("de")


def test_six_over_quota_logins_leave_the_breaker_closed_and_a_colleague_gets_through(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=FakeAutoi(token_status=400))

    for _ in range(resilience._FAILURE_THRESHOLD + 1):
        with pytest.raises(ProviderUserQuotaExceededError) as raised:
            _lookup(db_session, s, actor_id=s.user.id)
        # Still a refused login for every caller (Anto's option C: what the
        # user reads does not change).
        assert isinstance(raised.value, ProviderRejectedError)
        assert "user quota" in str(raised.value)

    assert resilience._circuit_for(s.connection.id).failure_count == 0
    assert resilience.is_circuit_open(s.connection.id) is False
    # Each refused login is logged as refused all the same.
    token_rows = (
        db_session.query(IntegrationCallLog)
        .filter_by(connection_id=s.connection.id, capability=OAUTH_TOKEN_OPERATION)
        .all()
    )
    assert len(token_rows) == resilience._FAILURE_THRESHOLD + 1
    assert {row.status for row in token_rows} == {CallStatus.ERROR}

    # A colleague auto-i does enrol is not locked out.
    s.fake.token_status = 200
    colleague = fx.make_user(db_session, tenant_id=s.connection.tenant_id, email="ben.colleague@example.test", first_name="Ben")
    assert _lookup(db_session, s, actor_id=colleague.id) is not None


def test_a_wrong_client_secret_still_charges_the_breaker(db_session, monkeypatch):
    """The connection's problem, not a user's: it keeps opening the breaker."""

    s = fx.connected(db_session, monkeypatch)
    s.secrets.values[(s.connection.id, "client_secret")] = "not-the-secret"

    for _ in range(resilience._FAILURE_THRESHOLD):
        with pytest.raises(ProviderRejectedError) as raised:
            _lookup(db_session, s, actor_id=s.user.id)
        assert not isinstance(raised.value, ProviderUserQuotaExceededError)

    assert resilience.is_circuit_open(s.connection.id) is True
