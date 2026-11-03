"""KAN-412 — ending a revoked connected account's grant at the provider.

KAN-201 built the revoke (the row marked, an auth event, an audit row) and
asked the provider's registered revoker after the commit, handing it the
platform ORM row, logging a failure at ERROR and never trying again. Now:

- the seam is `app.platform.public` — `register_grant_revoker` and the plain
  `RevokedGrant` DTO the revoker receives (id, provider, external account id,
  secret reference), so the integration context registers Microsoft's
  revoker without reading platform's model;
- every revoke (the user's DELETE, the manager's DELETE, deactivation's
  revoke-all) publishes `platform.connected_account.revoked` in its own
  transaction; the consumer `platform.connected_account_provider_revocation`
  runs the revoker with the DTO and, when it succeeds, records the end by
  dropping the row's `secret_ref` (an audit row says when). A revoker that
  raises makes the consumer raise: the outbox redelivers on its backoff and
  finally dead-letters, which alerts;
- nightly reconciliation reports a revoked grant that still holds a secret
  24 hours after the revoke — whether or not a revoker is registered for
  its provider.
"""

import dataclasses
import datetime as dt
import uuid

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.audit_model import AuditEvent
from app.core.consumer import Handler
from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.core.outbox_transport import InProcessTransport
from app.core.outbox_worker import MAX_ATTEMPTS, poll_once
from app.core.processed_event_model import ProcessedEvent
from app.core.reconciliation import ReconciliationAlarm
from app.core.reconciliation_model import ReconciliationOrphan
from app.platform import public as platform_public
from app.platform import reconciliation as platform_reconciliation
from app.platform.models.connected_account import ConnectedAccountStatus, UserConnectedAccount
from app.platform.models.user import User
from app.platform.public import ConnectedAccountProvider, RevokedGrant, register_grant_revoker
from app.platform.services import connected_account
from app.worker import register_handlers
from tests.identity_support import create_dealership, create_user, deactivate
from tests.test_my_account import _as, _grant

REVOKED_EVENT = "platform.connected_account.revoked"
CONSUMER = "platform.connected_account_provider_revocation"
ENDED_ACTION = "connected_account_provider_ended"
STALE_CHECK = "user_connected_account.revoked_grant_secret_still_held"


@pytest.fixture()
def revoker_calls():
    """A registered fake revoker, through platform.public — the integration
    context's door. `fail_first` makes it raise that many times first."""

    calls: list[RevokedGrant] = []
    state = {"fail_first": 0}

    def _revoker(grant: RevokedGrant) -> None:
        calls.append(grant)
        if state["fail_first"] > 0:
            state["fail_first"] -= 1
            raise RuntimeError("graph.microsoft.com is down")

    register_grant_revoker(ConnectedAccountProvider.MICROSOFT_365, _revoker)
    try:
        yield calls, state
    finally:
        platform_public.unregister_grant_revoker(ConnectedAccountProvider.MICROSOFT_365)


def _registered_consumer() -> tuple[str, Handler]:
    class _Recording(InProcessTransport):
        def __init__(self) -> None:
            super().__init__(session_factory=None)  # type: ignore[arg-type]
            self.seen: dict[str, tuple[str, Handler]] = {}

        def register(self, event_type: str, *, consumer_name: str, handler: Handler) -> None:
            self.seen[consumer_name] = (event_type, handler)

    recording = _Recording()
    register_handlers(recording)
    event_type, handler = recording.seen[CONSUMER]
    assert event_type == REVOKED_EVENT
    return event_type, handler


def _transport(engine) -> InProcessTransport:
    """The worker's own consumer for the event, alone on a transport."""

    event_type, handler = _registered_consumer()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    transport = InProcessTransport(factory)
    transport.register(event_type, consumer_name=CONSUMER, handler=handler)
    return transport


def _revoked_events(db: Session, account_id: uuid.UUID | None = None) -> list[OutboxMessage]:
    stmt = select(OutboxMessage).where(OutboxMessage.event_type == REVOKED_EVENT).order_by(OutboxMessage.id)
    if account_id is not None:
        stmt = stmt.where(OutboxMessage.aggregate_id == account_id)
    return list(db.scalars(stmt).all())


def _ended_audits(db: Session, account_id: uuid.UUID) -> int:
    rows = db.scalars(select(AuditEvent).where(AuditEvent.action == ENDED_ACTION)).all()
    return sum(1 for row in rows if (row.after or {}).get("connected_account_id") == str(account_id))


def _revoke_through_the_api(client, db_session, *, label: str = "anna@garage.ch"):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    grant = _grant(db_session, anna, label=label)
    response = client.delete(f"/v1/me/connected-accounts/{grant.id}", headers={**_as(anna), "If-Match": "1"})
    assert response.status_code == 200, response.text
    return anna, grant


def _poll_until_settled(db_session, engine, transport, message_id: uuid.UUID, *, rounds: int) -> OutboxMessage:
    """Polls the outbox `rounds` times, making the message due again before
    each poll so the backoff does not have to be waited out."""

    with Session(engine) as worker_db:
        for _ in range(rounds):
            message = worker_db.get(OutboxMessage, message_id, populate_existing=True)
            if message.status != OutboxStatus.PENDING:
                break
            message.next_attempt_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
            worker_db.commit()
            poll_once(worker_db, transport)
        message = worker_db.get(OutboxMessage, message_id, populate_existing=True)
        worker_db.expunge(message)
    db_session.expire_all()
    return message


# --- the seam in platform.public ---------------------------------------------------


def test_the_seam_is_in_platform_public():
    for name in ("RevokedGrant", "GrantRevoker", "ConnectedAccountProvider", "register_grant_revoker", "unregister_grant_revoker"):
        assert name in platform_public.__all__, name
        assert hasattr(platform_public, name), name


def test_the_dto_carries_exactly_the_agreed_fields_and_no_orm_row():
    assert dataclasses.is_dataclass(RevokedGrant)
    assert [field.name for field in dataclasses.fields(RevokedGrant)] == [
        "connected_account_id",
        "provider",
        "external_account_id",
        "secret_ref",
    ]
    grant = RevokedGrant(
        connected_account_id=uuid.uuid4(),
        provider=ConnectedAccountProvider.MICROSOFT_365,
        external_account_id="00000000-0000-0000-0000-00000000abcd",
        secret_ref="infisical://connected-accounts/x",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        grant.secret_ref = None  # type: ignore[misc]
    # The reference is a reference, not a secret — still, a log line of the
    # DTO does not print it.
    assert "infisical://" not in repr(grant)


def test_the_consumer_hands_the_registered_revoker_the_dto(client, db_session, engine, revoker_calls):
    calls, _ = revoker_calls
    _, grant = _revoke_through_the_api(client, db_session)
    assert calls == [], "the request asks no provider: the consumer does"
    [message] = _revoked_events(db_session, grant.id)

    _transport(engine).deliver(message)

    [seen] = calls
    assert type(seen) is RevokedGrant
    assert inspect(seen, raiseerr=False) is None, "an ORM object crossed the seam"
    assert seen == RevokedGrant(
        connected_account_id=grant.id,
        provider=ConnectedAccountProvider.MICROSOFT_365,
        external_account_id=grant.external_account_id,
        secret_ref=grant.secret_ref,
    )
    db_session.expire_all()
    row = db_session.get(UserConnectedAccount, grant.id)
    assert row.status == ConnectedAccountStatus.REVOKED
    assert row.secret_ref is None, "the provider's end is recorded by dropping the reference"
    assert row.version == 3  # 1 linked, 2 revoked, 3 ended at the provider
    assert _ended_audits(db_session, grant.id) == 1


# --- the event, in the revoke's own transaction ------------------------------------


def test_the_users_revoke_publishes_the_event_in_its_transaction(client, db_session):
    anna, grant = _revoke_through_the_api(client, db_session)

    [message] = _revoked_events(db_session)
    assert message.aggregate_type == "connected_account" and message.aggregate_id == grant.id
    assert message.tenant_id == uuid.UUID(anna["dealershipId"]) and message.producer == "platform"
    assert message.payload == {
        "connectedAccountId": str(grant.id),
        "userId": anna["id"],
        "provider": "microsoft_365",
        "reason": "user",
    }
    # Ids only: the consumer reads the reference from platform's own row.
    assert grant.secret_ref not in str(message.payload)


def test_a_rolled_back_revoke_publishes_nothing(client, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    grant = _grant(db_session, anna)
    user = db_session.get(User, uuid.UUID(anna["id"]))
    account = connected_account.get_for_update_or_404(db_session, user_id=user.id, account_id=grant.id)

    connected_account.revoke(db_session, account=account, user=user, actor_id=user.id, reason=connected_account.REASON_USER)
    assert len(_revoked_events(db_session)) == 1  # flushed inside the transaction
    db_session.rollback()

    assert _revoked_events(db_session) == []
    assert db_session.get(UserConnectedAccount, grant.id).status == ConnectedAccountStatus.ACTIVE


def test_the_managers_revoke_and_a_deactivation_publish_one_event_per_grant(client, db_session, revoker_calls):
    calls, _ = revoker_calls
    dealership = create_dealership(client)
    boss = create_user(client, dealership)
    anna = create_user(client, dealership, isDealerManager=False)
    first = _grant(db_session, anna)
    second = _grant(db_session, anna, label="kalender@garage.ch")
    third = _grant(db_session, anna, label="termine@garage.ch")

    revoked = client.delete(
        f"/v1/dealerships/{dealership}/users/{anna['id']}/connected-accounts/{first.id}",
        headers={**_as(boss, manager=True), "If-Match": "1"},
    )
    assert revoked.status_code == 200, revoked.text
    deactivated = deactivate(client, dealership, anna, if_match=anna["version"])
    assert deactivated.status_code == 200, deactivated.text

    events = _revoked_events(db_session)
    assert {(m.aggregate_id, m.payload["reason"]) for m in events} == {
        (first.id, "manager"),
        (second.id, "deactivated"),
        (third.id, "deactivated"),
    }
    assert len(events) == 3
    assert calls == [], "neither request asked the provider"


# --- retried by redelivery, recorded once ------------------------------------------


def test_a_failing_then_succeeding_provider_is_retried_and_the_end_recorded_once(
    client, db_session, engine, revoker_calls
):
    calls, state = revoker_calls
    state["fail_first"] = 2
    _, grant = _revoke_through_the_api(client, db_session)
    [message] = _revoked_events(db_session, grant.id)
    transport = _transport(engine)

    after_one = _poll_until_settled(db_session, engine, transport, message.id, rounds=1)
    assert after_one.status == OutboxStatus.PENDING and after_one.attempts == 1
    assert "graph.microsoft.com is down" in after_one.last_error
    row = db_session.get(UserConnectedAccount, grant.id)
    assert row.status == ConnectedAccountStatus.REVOKED and row.secret_ref == grant.secret_ref
    assert _ended_audits(db_session, grant.id) == 0

    settled = _poll_until_settled(db_session, engine, transport, message.id, rounds=5)

    assert settled.status == OutboxStatus.PUBLISHED and settled.attempts == 2
    assert len(calls) == 3
    row = db_session.get(UserConnectedAccount, grant.id)
    assert row.secret_ref is None and row.version == 3
    assert _ended_audits(db_session, grant.id) == 1

    # A redelivery after the success is a no-op: no provider call, no second record.
    transport.deliver(db_session.get(OutboxMessage, message.id))
    db_session.expire_all()
    assert len(calls) == 3
    assert _ended_audits(db_session, grant.id) == 1
    assert db_session.get(UserConnectedAccount, grant.id).version == 3
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ProcessedEvent)
            .where(ProcessedEvent.event_id == message.id, ProcessedEvent.consumer_name == CONSUMER)
        )
        == 1
    )


def test_a_provider_that_keeps_refusing_dead_letters_and_the_revoke_stands(
    client, db_session, engine, revoker_calls
):
    calls, state = revoker_calls
    state["fail_first"] = 1000
    _, grant = _revoke_through_the_api(client, db_session)
    [message] = _revoked_events(db_session, grant.id)

    dead = _poll_until_settled(db_session, engine, _transport(engine), message.id, rounds=MAX_ATTEMPTS + 1)

    assert dead.status == OutboxStatus.DEAD and dead.attempts == MAX_ATTEMPTS
    assert len(calls) == MAX_ATTEMPTS
    row = db_session.get(UserConnectedAccount, grant.id)
    assert row.status == ConnectedAccountStatus.REVOKED, "a provider never undoes a revoke"
    assert row.secret_ref == grant.secret_ref, "the reference stays so the secret can still be removed"
    assert _ended_audits(db_session, grant.id) == 0


def test_without_a_registered_revoker_the_consumer_records_nothing(client, db_session, engine, caplog):
    _, grant = _revoke_through_the_api(client, db_session)
    [message] = _revoked_events(db_session, grant.id)

    _transport(engine).deliver(message)

    db_session.expire_all()
    row = db_session.get(UserConnectedAccount, grant.id)
    assert row.status == ConnectedAccountStatus.REVOKED and row.secret_ref == grant.secret_ref and row.version == 2
    assert _ended_audits(db_session, grant.id) == 0
    assert any("no revoker for provider microsoft_365" in r.getMessage() for r in caplog.records)


# --- nightly reconciliation -----------------------------------------------------------


def _findings(db_session) -> set[uuid.UUID]:
    """This check's findings in a fresh run. The run raises on any finding,
    and the API's test tokens name actors that are no rows, so the actor
    checks may find something too: what matters here is this check."""

    try:
        run_id = platform_reconciliation.run(db_session).id
    except ReconciliationAlarm as alarm:
        run_id = alarm.run.id
    return set(
        db_session.scalars(
            select(ReconciliationOrphan.source_row_id).where(
                ReconciliationOrphan.run_id == run_id, ReconciliationOrphan.check_label == STALE_CHECK
            )
        ).all()
    )


def _revoked_row(db_session, anna: dict, *, hours_ago: float, secret: bool, label: str) -> UserConnectedAccount:
    row = _grant(db_session, anna, label=label)
    row.status = ConnectedAccountStatus.REVOKED
    row.revoked_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours_ago)
    row.revoked_by = uuid.UUID(anna["id"])
    row.revoked_reason = connected_account.REASON_USER
    if not secret:
        row.secret_ref = None
    db_session.commit()
    return row


def test_reconciliation_reports_a_revoked_grant_whose_secret_is_still_held(client, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    stale = _revoked_row(db_session, anna, hours_ago=25, secret=True, label="alt@garage.ch")
    fresh = _revoked_row(db_session, anna, hours_ago=1, secret=True, label="neu@garage.ch")
    ended = _revoked_row(db_session, anna, hours_ago=25, secret=False, label="beendet@garage.ch")
    live = _grant(db_session, anna, label="aktiv@garage.ch")

    findings = _findings(db_session)

    assert stale.id in findings
    assert fresh.id not in findings, "inside the grace period the outbox is still retrying"
    assert ended.id not in findings, "the provider's end is recorded"
    assert live.id not in findings, "a live grant holds its secret by right"
    assert findings == {stale.id}


def test_reconciliation_is_clean_once_the_provider_ended_the_grant(client, db_session, engine, revoker_calls):
    """The whole loop: the revoke, the consumer, then a clean night."""

    _, grant = _revoke_through_the_api(client, db_session)
    stale = dt.datetime.now(dt.UTC) - dt.timedelta(hours=25)
    db_session.execute(
        UserConnectedAccount.__table__.update().where(UserConnectedAccount.id == grant.id).values(revoked_at=stale)
    )
    db_session.commit()
    [message] = _revoked_events(db_session, grant.id)

    assert _findings(db_session) == {grant.id}
    _transport(engine).deliver(message)
    db_session.expire_all()

    assert _findings(db_session) == set()
