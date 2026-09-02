"""WP-6 PR-6: ADR-024's three retention tiers — call metadata 24 months,
error payloads 30 days, successful payloads 7 days. Each purge is a
strict "older than" cutoff, verified on both sides of the boundary.
"""

import datetime as dt
import uuid

import pytest
import sqlalchemy as sa

from app.core.types import GUID
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.call_payload import IntegrationCallPayload, PayloadKind
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import retention

TODAY = dt.date(2026, 8, 31)


def _make_provider(db_session) -> IntegrationProvider:
    # provider_code is globally unique, and a single test here often calls
    # _make_call_log (hence _make_provider) more than once — a fresh
    # per-call suffix avoids colliding with itself, not just with other
    # tests.
    provider = IntegrationProvider(
        provider_code=f"auto_i_dat_mock_{uuid.uuid4().hex[:8]}", category="vehicle_data",
        display_name="auto-i-dat (mock)", auth_type="none", required_secret_slots=[],
        capability_codes=["vehicle_data"],
    )
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


def _make_connection(db_session, *, tenant_id):
    provider = _make_provider(db_session)
    return connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )


def _make_call_log(db_session, *, age_days: int) -> IntegrationCallLog:
    # A real connection row — integration_call_log.connection_id carries a
    # genuine FK (see that model's own migration), enforced by Postgres
    # even though SQLite's fast lane never checks it. Each call creates
    # its own connection/provider pair rather than sharing one, since nothing
    # here needs to correlate calls by connection.
    tenant_id = uuid.uuid4()
    connection = _make_connection(db_session, tenant_id=tenant_id)
    log = IntegrationCallLog(
        connection_id=connection.id, tenant_id=tenant_id, capability="vehicle_data", status=CallStatus.SUCCESS,
        duration_ms=42, correlation_id=uuid.uuid4(),
    )
    log.created_at = dt.datetime.combine(TODAY - dt.timedelta(days=age_days), dt.time.min, tzinfo=dt.UTC)
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)
    return log


def _make_payload(db_session, *, call_log: IntegrationCallLog, kind: PayloadKind, age_days: int) -> IntegrationCallPayload:
    row = IntegrationCallPayload(call_log_id=call_log.id, tenant_id=call_log.tenant_id, kind=kind, payload="<xml>demo</xml>")
    row.created_at = dt.datetime.combine(TODAY - dt.timedelta(days=age_days), dt.time.min, tzinfo=dt.UTC)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


# --- capture --------------------------------------------------------------


def test_capture_call_payload_is_encrypted_at_rest_but_reads_back_plain(db_session):
    log = _make_call_log(db_session, age_days=0)
    row = retention.capture_call_payload(db_session, call_log=log, kind=PayloadKind.SUCCESS, payload="raw content")
    db_session.commit()

    # The EncryptedString column stores ciphertext — read the raw DB value
    # via a fresh, uninstrumented connection to prove it's never plaintext.
    # `text()` with a named bind param, never `exec_driver_sql`'s own
    # driver-specific placeholder syntax (SQLite's `?` isn't Postgres's
    # `%s`) — this needs to run correctly against both.
    raw_stored = db_session.execute(
        sa.text("SELECT payload FROM integration_call_payload WHERE id = :id").bindparams(
            sa.bindparam("id", type_=GUID())
        ),
        {"id": row.id},
    ).scalar()
    assert raw_stored != "raw content"

    db_session.refresh(row)
    assert row.payload == "raw content"  # the ORM's own read path decrypts transparently


# --- purge tiers ------------------------------------------------------------


def test_call_log_metadata_survives_at_23_months_purges_at_25(db_session):
    survivor = _make_call_log(db_session, age_days=23 * 30)
    victim = _make_call_log(db_session, age_days=25 * 30)

    deleted = retention.purge_call_log_metadata(db_session, today=TODAY)

    assert deleted == 1
    assert db_session.get(IntegrationCallLog, survivor.id) is not None
    assert db_session.get(IntegrationCallLog, victim.id) is None


def test_error_payload_survives_at_29_days_purges_at_31(db_session):
    survivor = _make_payload(db_session, call_log=_make_call_log(db_session, age_days=0), kind=PayloadKind.ERROR, age_days=29)
    victim = _make_payload(db_session, call_log=_make_call_log(db_session, age_days=0), kind=PayloadKind.ERROR, age_days=31)

    deleted = retention.purge_error_payloads(db_session, today=TODAY)

    assert deleted == 1
    assert db_session.get(IntegrationCallPayload, survivor.id) is not None
    assert db_session.get(IntegrationCallPayload, victim.id) is None


def test_success_payload_survives_at_6_days_purges_at_8(db_session):
    survivor = _make_payload(db_session, call_log=_make_call_log(db_session, age_days=0), kind=PayloadKind.SUCCESS, age_days=6)
    victim = _make_payload(db_session, call_log=_make_call_log(db_session, age_days=0), kind=PayloadKind.SUCCESS, age_days=8)

    deleted = retention.purge_success_payloads(db_session, today=TODAY)

    assert deleted == 1
    assert db_session.get(IntegrationCallPayload, survivor.id) is not None
    assert db_session.get(IntegrationCallPayload, victim.id) is None


def test_purge_never_touches_the_wrong_kind(db_session):
    """An 8-day-old ERROR payload must survive `purge_success_payloads` —
    the two tiers are independent, never a shared cutoff."""

    log = _make_call_log(db_session, age_days=0)
    error_row = _make_payload(db_session, call_log=log, kind=PayloadKind.ERROR, age_days=8)

    deleted = retention.purge_success_payloads(db_session, today=TODAY)

    assert deleted == 0
    assert db_session.get(IntegrationCallPayload, error_row.id) is not None


# --- break-glass lookup -----------------------------------------------------


def test_get_call_payload_or_404_raises_for_a_missing_row(db_session):
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        retention.get_call_payload_or_404(db_session, payload_id=uuid.uuid4())
