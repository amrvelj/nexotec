"""KAN-216 — staff notifications (Dealer Administration D-A-40, FR-A-47…50;
Authentication & Identity FR-AU-12, FR-AU-26): the catalogue, `notify`, the
inbox, the per-family preferences, the daily digest and purge, Web Push
subscriptions bound to the session and the push consumer, and the
`security` family wired at its sources."""

import dataclasses
import datetime as dt
import uuid

import pytest
from sqlalchemy import func, insert, select, text

from app.core.outbox_model import OutboxMessage
from app.core.uuid7 import uuid7
from app.platform import consumers
from app.platform.models.auth_event import AuthEventKind
from app.platform.models.auth_session import AuthSession
from app.platform.models.notification import (
    Notification,
    NotificationEvent,
    NotificationFamily,
    NotificationTiming,
    PushSubscription,
)
from app.platform.public import NotificationEventCode, NotificationSubject, UnknownNotificationEvent, notify
from app.platform.reference.services import web_push
from app.platform.reference.services.web_push import PushMessage, PushOutcome, PushTarget
from app.platform.services import auth_events, notifications, push_subscriptions
from tests.identity_support import (
    STRONG_PASSWORD,
    PasswordLogin,
    cookie_headers,
    create_dealership,
    create_user,
    last_mail_to,
    recover,
    token_in,
)
from tests.test_identity_login_policy import _fail_patiently

_PROBE = "administration.kan216_probe"
_SUBJECT = NotificationSubject(type="user", link="/account/security")


@pytest.fixture()
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


@pytest.fixture()
def probe_event(engine):
    """A switchable catalogue entry in a family that has no event yet in v1
    (`administration`) — the test's own row, so the timing rules can be
    exercised before any such event ships."""

    with engine.begin() as conn:
        conn.execute(
            insert(NotificationEvent.__table__).values(
                id=uuid7(),
                code=_PROBE,
                family="administration",
                default_timing="immediate",
                switchable=True,
                title_key="notifications.events.newDeviceLogin.title",
                body_key="notifications.events.newDeviceLogin.body",
            )
        )
    return _PROBE


@dataclasses.dataclass
class RecordingSender:
    key: str | None = "BPublicVapidKeyForTests"
    outcome: PushOutcome = PushOutcome.DELIVERED
    sent: list[tuple[PushTarget, PushMessage]] = dataclasses.field(default_factory=list)

    def public_key(self) -> str | None:
        return self.key

    def send(self, target: PushTarget, message: PushMessage) -> PushOutcome:
        self.sent.append((target, message))
        return self.outcome


@pytest.fixture()
def sender():
    recording = RecordingSender()
    web_push.register_web_push_sender(recording)
    try:
        yield recording
    finally:
        web_push.reset_web_push_sender()


def _user_row(client, login) -> tuple[dict, str]:
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    return anna, cookie


def _inbox(db_session, user: dict) -> list[Notification]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(Notification).where(Notification.user_id == uuid.UUID(user["id"])).order_by(Notification.created_at)
        ).all()
    )


def _codes(db_session, user: dict) -> list[str]:
    return [n.event_code for n in _inbox(db_session, user)]


def _outbox(db_session, event_type: str) -> list[OutboxMessage]:
    db_session.expire_all()
    return list(db_session.scalars(select(OutboxMessage).where(OutboxMessage.event_type == event_type)).all())


def _subscribe(client, cookie: str, endpoint: str = "https://push.example.test/send/abc") -> dict:
    response = client.post(
        "/v1/me/push-subscriptions",
        json={"endpoint": endpoint, "keys": {"p256dh": "BPeerKey", "auth": "authsecret"}},
        headers=cookie_headers(cookie),
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- the catalogue and notify() (FR-A-47) ------------------------------------------------


def test_a_code_outside_the_catalogue_cannot_be_sent(client, db_session):
    anna = create_user(client, create_dealership(client))
    with pytest.raises(UnknownNotificationEvent):
        notify(db_session, "security.not_registered", user_id=uuid.UUID(anna["id"]), subject=_SUBJECT)
    db_session.rollback()
    assert _inbox(db_session, anna) == []


def test_notify_writes_the_inbox_row_and_its_outbox_row_in_the_callers_transaction(client, db_session):
    anna = create_user(client, create_dealership(client))
    row = notify(db_session, NotificationEventCode.ACCOUNT_LOCKED, user_id=uuid.UUID(anna["id"]), subject=_SUBJECT)
    assert row is not None and row.delivered_at is not None
    # Nothing commits on its own: a rolled-back action owes nobody a notification.
    db_session.rollback()
    assert _inbox(db_session, anna) == []
    assert _outbox(db_session, notifications.DELIVERED_EVENT) == []

    notify(db_session, NotificationEventCode.ACCOUNT_LOCKED, user_id=uuid.UUID(anna["id"]), subject=_SUBJECT)
    db_session.commit()
    assert _codes(db_session, anna) == ["security.account_locked"]
    [message] = _outbox(db_session, notifications.DELIVERED_EVENT)
    # Ids and the code only — never a title, body or parameter.
    assert set(message.payload) == {"notificationId", "userId", "eventCode"}


def test_notify_refuses_free_text_and_absolute_links(client, db_session):
    anna = create_user(client, create_dealership(client))
    user_id = uuid.UUID(anna["id"])
    with pytest.raises(ValueError, match="SPA path"):
        notify(
            db_session,
            NotificationEventCode.ACCOUNT_LOCKED,
            user_id=user_id,
            subject=NotificationSubject(type="user", link="https://evil.example/"),
        )
    with pytest.raises(ValueError, match="closed codes"):
        notify(
            db_session,
            NotificationEventCode.ACCOUNT_LOCKED,
            user_id=user_id,
            subject=_SUBJECT,
            params={"reason": "x" * 200},
        )
    db_session.rollback()


# --- the inbox (FR-A-50) ---------------------------------------------------------------


def test_the_inbox_lists_my_notifications_newest_first_and_marks_them_read(client, login, db_session):
    anna, cookie = _user_row(client, login)
    bob = create_user(client, anna["dealershipId"])
    for code in (NotificationEventCode.ACCOUNT_LOCKED, NotificationEventCode.PASSWORD_CHANGED):
        notify(db_session, code, user_id=uuid.UUID(anna["id"]), subject=_SUBJECT)
    bobs = notify(db_session, NotificationEventCode.ACCOUNT_LOCKED, user_id=uuid.UUID(bob["id"]), subject=_SUBJECT)
    db_session.commit()

    page = client.get("/v1/me/notifications", headers=cookie_headers(cookie))
    assert page.status_code == 200, page.text
    items = page.json()["items"]
    assert [i["eventCode"] for i in items] == ["security.password_changed", "security.account_locked"]
    first = items[0]
    assert first["family"] == "security"
    assert first["titleKey"] == "notifications.events.passwordChanged.title"
    assert first["link"] == "/account/security" and first["readAt"] is None
    assert client.get("/v1/me/notifications/unread-count", headers=cookie_headers(cookie)).json() == {"count": 2}

    read = client.patch(f"/v1/me/notifications/{first['id']}", json={"read": True}, headers=cookie_headers(cookie))
    assert read.status_code == 200, read.text and read.json()["readAt"] is not None
    unread = client.get("/v1/me/notifications?unread=true", headers=cookie_headers(cookie)).json()["items"]
    assert [i["eventCode"] for i in unread] == ["security.account_locked"]
    again = client.patch(f"/v1/me/notifications/{first['id']}", json={"read": False}, headers=cookie_headers(cookie))
    assert again.json()["readAt"] is None

    # Someone else's notification is not there (404, never 403).
    assert (
        client.patch(f"/v1/me/notifications/{bobs.id}", json={"read": True}, headers=cookie_headers(cookie)).status_code
        == 404
    )

    marked = client.post("/v1/me/notifications/read-all", headers=cookie_headers(cookie))
    assert marked.status_code == 200 and marked.json() == {"marked": 2}
    assert client.get("/v1/me/notifications/unread-count", headers=cookie_headers(cookie)).json() == {"count": 0}
    # Bob's stays unread.
    db_session.expire_all()
    assert db_session.get(Notification, bobs.id).read_at is None


def test_the_inbox_pages_with_a_cursor(client, login, db_session):
    anna, cookie = _user_row(client, login)
    for _ in range(3):
        notify(db_session, NotificationEventCode.ACCOUNT_LOCKED, user_id=uuid.UUID(anna["id"]), subject=_SUBJECT)
    db_session.commit()
    first = client.get("/v1/me/notifications?limit=2", headers=cookie_headers(cookie)).json()
    assert len(first["items"]) == 2 and first["nextCursor"]
    second = client.get(f"/v1/me/notifications?limit=2&cursor={first['nextCursor']}", headers=cookie_headers(cookie))
    assert len(second.json()["items"]) == 1 and second.json()["nextCursor"] is None
    ids = {i["id"] for i in first["items"]} | {i["id"] for i in second.json()["items"]}
    assert len(ids) == 3


def test_the_purge_keeps_ninety_days(client, db_session):
    anna = create_user(client, create_dealership(client))
    old = notify(db_session, NotificationEventCode.ACCOUNT_LOCKED, user_id=uuid.UUID(anna["id"]), subject=_SUBJECT)
    recent = notify(db_session, NotificationEventCode.PASSWORD_CHANGED, user_id=uuid.UUID(anna["id"]), subject=_SUBJECT)
    old.created_at = old.created_at - dt.timedelta(days=91)
    recent.created_at = recent.created_at - dt.timedelta(days=89)
    db_session.commit()

    from app.platform.daily_jobs import run_daily_notification_purge

    run_daily_notification_purge(db_session)
    assert _codes(db_session, anna) == ["security.password_changed"]


# --- preferences (FR-A-48) ---------------------------------------------------------------


def test_preferences_list_every_family_with_the_platform_default(client, login):
    _, cookie = _user_row(client, login)
    response = client.get("/v1/me/notification-preferences", headers=cookie_headers(cookie))
    assert response.status_code == 200, response.text
    items = {i["family"]: i for i in response.json()["items"]}
    assert set(items) == {"security", "administration", "assignment"}
    assert items["security"] == {
        "family": "security",
        "timing": "immediate",
        "inApp": True,
        "push": True,
        "switchable": False,
        "hasEvents": True,
        "version": 0,
    }
    assert items["administration"]["switchable"] is True and items["administration"]["hasEvents"] is False


def test_the_security_family_cannot_be_changed(client, login):
    _, cookie = _user_row(client, login)
    for body in ({"timing": "off", "push": True}, {"timing": "immediate", "push": False}):
        response = client.put(
            "/v1/me/notification-preferences/security", json=body, headers={**cookie_headers(cookie), "If-Match": "0"}
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"]["rule"] == "family_locked"


def test_a_family_preference_is_saved_with_if_match(client, login):
    _, cookie = _user_row(client, login)
    path = "/v1/me/notification-preferences/administration"
    body = {"timing": "digest", "push": False}
    assert client.put(path, json=body, headers=cookie_headers(cookie)).status_code == 400  # If-Match required
    first = client.put(path, json=body, headers={**cookie_headers(cookie), "If-Match": "0"})
    assert first.status_code == 200, first.text
    assert first.json()["timing"] == "digest" and first.json()["push"] is False and first.json()["version"] == 1
    stale = client.put(path, json={"timing": "off", "push": False}, headers={**cookie_headers(cookie), "If-Match": "0"})
    assert stale.status_code == 409, stale.text
    second = client.put(
        path, json={"timing": "off", "push": False}, headers={**cookie_headers(cookie), "If-Match": "1"}
    )
    assert second.status_code == 200 and second.json()["version"] == 2
    # The inbox is the record: in-app cannot be switched off.
    refused = client.put(
        path,
        json={"timing": "immediate", "inApp": False, "push": True},
        headers={**cookie_headers(cookie), "If-Match": "2"},
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["details"]["rule"] == "in_app_is_the_record"


def test_off_writes_nothing_and_digest_holds_until_the_daily_job(client, login, db_session, probe_event):
    anna, cookie = _user_row(client, login)
    user_id = uuid.UUID(anna["id"])
    path = "/v1/me/notification-preferences/administration"
    assert (
        client.put(
            path, json={"timing": "off", "push": True}, headers={**cookie_headers(cookie), "If-Match": "0"}
        ).status_code
        == 200
    )
    assert notify(db_session, probe_event, user_id=user_id, subject=_SUBJECT) is None
    db_session.commit()
    assert _inbox(db_session, anna) == []

    assert (
        client.put(
            path, json={"timing": "digest", "push": True}, headers={**cookie_headers(cookie), "If-Match": "1"}
        ).status_code
        == 200
    )
    held = notify(db_session, probe_event, user_id=user_id, subject=_SUBJECT)
    # A security event in the same day is not held: it is not switchable.
    notify(db_session, NotificationEventCode.ACCOUNT_LOCKED, user_id=user_id, subject=_SUBJECT)
    db_session.commit()
    assert held is not None and held.delivered_at is None
    listed = client.get("/v1/me/notifications", headers=cookie_headers(cookie)).json()["items"]
    assert [i["eventCode"] for i in listed] == ["security.account_locked"]
    assert (
        client.patch(f"/v1/me/notifications/{held.id}", json={"read": True}, headers=cookie_headers(cookie)).status_code
        == 404
    )
    assert _outbox(db_session, notifications.DELIVERED_EVENT)[0].payload["eventCode"] == "security.account_locked"

    from app.platform.daily_jobs import run_daily_notification_digest

    run_daily_notification_digest(db_session)
    listed = client.get("/v1/me/notifications", headers=cookie_headers(cookie)).json()["items"]
    assert {i["eventCode"] for i in listed} == {"security.account_locked", probe_event}
    [digest] = _outbox(db_session, notifications.DIGEST_RELEASED_EVENT)
    assert digest.payload == {"userId": anna["id"], "count": 1, "families": ["administration"]}
    # Released once: the next run finds nothing held.
    run_daily_notification_digest(db_session)
    assert len(_outbox(db_session, notifications.DIGEST_RELEASED_EVENT)) == 1


def test_a_digest_spanning_two_betriebe_is_one_event_filed_under_no_betrieb(client, login, db_session, probe_event):
    """KAN-519 (Anto, 2026-11-03, option A): someone who works in two
    Betriebe gets one digest for the whole day — one event, one push — and the
    event names no Betrieb. It used to take the first held row's Betrieb,
    which misfiled the other's notifications."""

    home_betrieb = create_dealership(client)
    anna = create_user(client, home_betrieb)
    cookie = login.session_cookie(anna)
    user_id = uuid.UUID(anna["id"])
    sister = uuid.UUID(create_dealership(client, legalName="Garage Schwester AG"))
    assert (
        client.put(
            "/v1/me/notification-preferences/administration",
            json={"timing": "digest", "push": True},
            headers={**cookie_headers(cookie), "If-Match": "0"},
        ).status_code
        == 200
    )
    home = notify(db_session, probe_event, user_id=user_id, subject=_SUBJECT, dealership_id=uuid.UUID(home_betrieb))
    elsewhere = notify(db_session, probe_event, user_id=user_id, subject=_SUBJECT, dealership_id=sister)
    assert home is not None and elsewhere is not None and home.delivered_at is None
    db_session.commit()
    assert {home.dealership_id, elsewhere.dealership_id} == {uuid.UUID(home_betrieb), sister}

    assert notifications.release_digests(db_session) == 1
    db_session.commit()

    [digest] = _outbox(db_session, notifications.DIGEST_RELEASED_EVENT)
    assert digest.tenant_id is None
    assert digest.payload == {"userId": anna["id"], "count": 2, "families": ["administration"]}


# --- Web Push subscriptions (FR-A-49) ----------------------------------------------------


def test_push_is_unavailable_until_a_sender_is_configured(client, login):
    _, cookie = _user_row(client, login)
    assert client.get("/v1/me/push/config", headers=cookie_headers(cookie)).json() == {
        "available": False,
        "vapidPublicKey": None,
    }


def test_push_config_names_the_senders_key(client, login, sender):
    _, cookie = _user_row(client, login)
    assert client.get("/v1/me/push/config", headers=cookie_headers(cookie)).json() == {
        "available": True,
        "vapidPublicKey": "BPublicVapidKeyForTests",
    }


def test_a_subscription_is_bound_to_the_session_and_goes_when_the_session_is_revoked(client, login, db_session):
    _, cookie = _user_row(client, login)
    created = _subscribe(client, cookie)
    db_session.expire_all()
    row = db_session.get(PushSubscription, uuid.UUID(created["id"]))
    assert row is not None and str(row.session_id) == created["sessionId"]
    # The keys are encrypted at rest, never returned.
    assert "endpoint" not in created and "keys" not in created
    raw = db_session.execute(text("SELECT auth FROM push_subscription")).scalar_one()
    assert raw != "authsecret"

    assert client.post("/v1/auth/logout", headers=cookie_headers(cookie)).status_code == 200
    db_session.expire_all()
    assert db_session.scalar(select(func.count(PushSubscription.id))) == 0


def test_every_revoked_session_loses_its_subscription_and_a_live_one_keeps_it(client, login, db_session):
    anna, first = _user_row(client, login)
    second = login.session_cookie(anna)
    _subscribe(client, first, "https://push.example.test/send/first")
    kept = _subscribe(client, second, "https://push.example.test/send/second")
    first_sid = db_session.scalar(
        select(PushSubscription.session_id).where(PushSubscription.id != uuid.UUID(kept["id"]))
    )
    # The user ends the other session from My account.
    response = client.delete(f"/v1/auth/sessions/{first_sid}", headers=cookie_headers(second))
    assert response.status_code == 204, response.text
    db_session.expire_all()
    assert [str(i) for i in db_session.scalars(select(PushSubscription.id)).all()] == [kept["id"]]

    # Platform staff end every session: the last subscription goes too.
    assert recover(client, anna["id"], "revoke-sessions").status_code == 200
    db_session.expire_all()
    assert db_session.scalar(select(func.count(PushSubscription.id))) == 0


def test_the_same_browser_registering_again_moves_to_the_new_session(client, login, db_session):
    anna, first = _user_row(client, login)
    one = _subscribe(client, first)
    second = login.session_cookie(anna)
    two = _subscribe(client, second)
    assert one["id"] == two["id"] and one["sessionId"] != two["sessionId"]
    db_session.expire_all()
    assert db_session.scalar(select(func.count(PushSubscription.id))) == 1


def test_a_subscription_needs_https_and_a_session_and_is_mine_alone(client, login, db_session):
    anna, cookie = _user_row(client, login)
    refused = client.post(
        "/v1/me/push-subscriptions",
        json={"endpoint": "http://push.example.test/x", "keys": {"p256dh": "a", "auth": "b"}},
        headers=cookie_headers(cookie),
    )
    assert refused.status_code == 422 and refused.json()["error"]["details"]["rule"] == "endpoint"

    from tests.test_my_account import _as

    no_session = client.post(
        "/v1/me/push-subscriptions",
        json={"endpoint": "https://push.example.test/x", "keys": {"p256dh": "a", "auth": "b"}},
        headers=_as(anna),
    )
    assert no_session.status_code == 422 and no_session.json()["error"]["details"]["rule"] == "session_required"

    mine = _subscribe(client, cookie)
    bob = create_user(client, anna["dealershipId"])
    bobs = login.session_cookie(bob)
    assert client.delete(f"/v1/me/push-subscriptions/{mine['id']}", headers=cookie_headers(bobs)).status_code == 404
    assert client.delete(f"/v1/me/push-subscriptions/{mine['id']}", headers=cookie_headers(cookie)).status_code == 204


def test_a_subscription_post_honours_the_idempotency_key(client, login, db_session):
    _, cookie = _user_row(client, login)
    body = {"endpoint": "https://push.example.test/send/idem", "keys": {"p256dh": "a", "auth": "b"}}
    headers = {**cookie_headers(cookie), "Idempotency-Key": str(uuid.uuid4())}
    first = client.post("/v1/me/push-subscriptions", json=body, headers=headers)
    replay = client.post("/v1/me/push-subscriptions", json=body, headers=headers)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()


# --- the push consumer: a pointer, title and link only (FR-A-50) ----------------------


def _delivered_message(db_session, row: Notification) -> OutboxMessage:
    return db_session.scalar(
        select(OutboxMessage).where(
            OutboxMessage.event_type == notifications.DELIVERED_EVENT, OutboxMessage.aggregate_id == row.id
        )
    )


def test_a_delivered_notification_pushes_its_title_and_link_to_live_subscriptions(client, login, db_session, sender):
    anna, cookie = _user_row(client, login)
    _subscribe(client, cookie)
    row = notify(db_session, NotificationEventCode.FACTOR_REMOVED, user_id=uuid.UUID(anna["id"]), subject=_SUBJECT)
    db_session.commit()

    consumers.handle_notification_delivered_message(db_session, _delivered_message(db_session, row))

    [(target, message)] = sender.sent
    assert target.endpoint == "https://push.example.test/send/abc" and target.auth == "authsecret"
    # German, the default UI language; nothing but the title and the link.
    assert message.as_payload() == {"title": "Zweiter Faktor entfernt", "link": "/account/security"}


def test_the_push_title_follows_the_ui_language(client, login, db_session, sender):
    anna, cookie = _user_row(client, login)
    put = client.put("/v1/me/preferences/ui", json={"uiLanguage": "fr"}, headers=cookie_headers(cookie))
    assert put.status_code in (200, 204), put.text
    _subscribe(client, cookie)
    row = notify(db_session, NotificationEventCode.FACTOR_REMOVED, user_id=uuid.UUID(anna["id"]), subject=_SUBJECT)
    db_session.commit()
    consumers.handle_notification_delivered_message(db_session, _delivered_message(db_session, row))
    assert sender.sent[0][1].title == "Deuxième facteur supprimé"


def test_no_push_for_a_read_notification_a_family_without_push_or_no_sender(
    client, login, db_session, sender, probe_event
):
    anna, cookie = _user_row(client, login)
    _subscribe(client, cookie)
    user_id = uuid.UUID(anna["id"])
    read = notify(db_session, NotificationEventCode.ACCOUNT_LOCKED, user_id=user_id, subject=_SUBJECT)
    read.read_at = read.created_at
    db_session.commit()
    consumers.handle_notification_delivered_message(db_session, _delivered_message(db_session, read))

    assert (
        client.put(
            "/v1/me/notification-preferences/administration",
            json={"timing": "immediate", "push": False},
            headers={**cookie_headers(cookie), "If-Match": "0"},
        ).status_code
        == 200
    )
    silent = notify(db_session, probe_event, user_id=user_id, subject=_SUBJECT)
    db_session.commit()
    consumers.handle_notification_delivered_message(db_session, _delivered_message(db_session, silent))
    assert sender.sent == []

    sender.key = None  # no VAPID key: nothing is sent at all
    pushed = notify(db_session, NotificationEventCode.ACCOUNT_LOCKED, user_id=user_id, subject=_SUBJECT)
    db_session.commit()
    consumers.handle_notification_delivered_message(db_session, _delivered_message(db_session, pushed))
    assert sender.sent == []


def test_a_subscription_the_push_service_reports_gone_is_deleted(client, login, db_session, sender):
    anna, cookie = _user_row(client, login)
    _subscribe(client, cookie)
    sender.outcome = PushOutcome.GONE
    row = notify(db_session, NotificationEventCode.ACCOUNT_LOCKED, user_id=uuid.UUID(anna["id"]), subject=_SUBJECT)
    db_session.commit()
    consumers.handle_notification_delivered_message(db_session, _delivered_message(db_session, row))
    db_session.commit()
    assert db_session.scalar(select(func.count(PushSubscription.id))) == 0


def test_the_digest_pushes_one_pointer_to_the_inbox(client, login, db_session, sender, probe_event):
    anna, cookie = _user_row(client, login)
    _subscribe(client, cookie)
    assert (
        client.put(
            "/v1/me/notification-preferences/administration",
            json={"timing": "digest", "push": True},
            headers={**cookie_headers(cookie), "If-Match": "0"},
        ).status_code
        == 200
    )
    for _ in range(2):
        notify(db_session, probe_event, user_id=uuid.UUID(anna["id"]), subject=_SUBJECT)
    db_session.commit()
    notifications.release_digests(db_session)
    db_session.commit()
    [message] = _outbox(db_session, notifications.DIGEST_RELEASED_EVENT)
    consumers.handle_digest_released_message(db_session, message)
    assert [m.as_payload() for _, m in sender.sent] == [{"title": "Neue Benachrichtigungen", "link": "/notifications"}]


# --- the security family at its sources (FR-AU-12, FR-AU-26) -----------------------------


def _login_as(client, user: dict, *, agent: str):
    response = client.post(
        "/v1/auth/login", json={"email": user["email"], "password": STRONG_PASSWORD}, headers={"User-Agent": agent}
    )
    assert response.status_code == 200, response.text
    return response.cookies.get("dms_session")


def test_a_login_from_a_new_device_notifies_and_a_known_one_does_not(client, login, db_session, mailbox):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)  # the first session ever: no stranger's
    assert _codes(db_session, anna) == []
    _login_as(client, anna, agent="testclient")
    assert _codes(db_session, anna) == []
    _login_as(client, anna, agent="Mozilla/5.0 (Macintosh) Firefox/140.0")
    assert _codes(db_session, anna) == ["security.new_device_login"]
    _login_as(client, anna, agent="Mozilla/5.0 (Macintosh) Firefox/140.0")
    assert _codes(db_session, anna) == ["security.new_device_login"]
    # Never an e-mail (D-A-40): the only mail Anna got is her invitation.
    assert [m.to for m in mailbox] == [anna["email"]]


def test_a_lockout_notifies_the_user(client, login, db_session, mailbox):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    mails_before = len(mailbox)
    _fail_patiently(client, db_session, anna, times=5)
    assert _codes(db_session, anna) == ["security.account_locked"]
    assert len(mailbox) == mails_before


def test_a_password_reset_notifies_and_the_first_password_does_not(client, login, db_session, mailbox):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    assert _codes(db_session, anna) == []
    assert client.post("/v1/auth/password-reset/request", json={"email": anna["email"]}).status_code == 202
    token = token_in(last_mail_to(mailbox, anna["email"]))
    confirmed = client.post(
        "/v1/auth/password-reset/confirm", json={"token": token, "password": "a-brand-new-password-2026"}
    )
    assert confirmed.status_code == 204, confirmed.text
    assert _codes(db_session, anna) == ["security.password_changed"]


def test_enrolling_using_a_recovery_code_and_removing_a_factor_notify(client, login, db_session):
    from tests.test_identity_mfa import _enrol_totp

    anna, cookie = _user_row(client, login)
    secret, codes = _enrol_totp(client, cookie)
    assert _codes(db_session, anna) == ["security.factor_enrolled"]

    step = client.post("/v1/auth/login", json={"email": anna["email"], "password": STRONG_PASSWORD})
    assert step.json()["mfaRequired"] is True, step.text
    verified = client.post("/v1/auth/mfa/verify", json={"challengeId": step.json()["challengeId"], "code": codes[0]})
    assert verified.status_code == 200, verified.text
    assert "security.recovery_code_used" in _codes(db_session, anna)
    used = next(n for n in _inbox(db_session, anna) if n.event_code == "security.recovery_code_used")
    assert used.params == {"remaining": 9}

    removed = client.delete("/v1/auth/mfa/totp", headers=cookie_headers(verified.cookies.get("dms_session")))
    assert removed.status_code in (200, 204), removed.text
    assert _codes(db_session, anna)[-1] == "security.factor_removed"
    del secret


def test_platform_recovery_notifies_the_action_and_the_sessions_ended_by_someone_else(client, login, db_session):
    anna, _ = _user_row(client, login)
    assert recover(client, anna["id"], "revoke-sessions").status_code == 200
    rows = _inbox(db_session, anna)
    assert sorted(n.event_code for n in rows) == [
        "security.platform_recovery_action",
        "security.session_revoked_by_other",
    ]
    action = next(n for n in rows if n.event_code == "security.platform_recovery_action")
    # The action as a closed code — never the stated reason.
    assert action.params == {"action": "sessionsRevoked"}
    assert "ticket" not in str([n.params for n in rows])


def test_ending_my_own_session_is_not_someone_elses_doing(client, login, db_session):
    anna, first = _user_row(client, login)
    second = login.session_cookie(anna)
    sid = db_session.scalar(
        select(AuthSession.id)
        .where(AuthSession.user_id == uuid.UUID(anna["id"]))
        .order_by(AuthSession.created_at.desc())
    )
    assert client.delete(f"/v1/auth/sessions/{sid}", headers=cookie_headers(first)).status_code == 204
    assert client.post("/v1/auth/logout", headers=cookie_headers(first)).status_code == 200
    assert "security.session_revoked_by_other" not in _codes(db_session, anna)
    del second


def test_a_connected_account_revoke_notifies(client, login, db_session):
    from tests.test_my_account import _grant

    anna, cookie = _user_row(client, login)
    grant = _grant(db_session, anna)
    response = client.delete(
        f"/v1/me/connected-accounts/{grant.id}", headers={**cookie_headers(cookie), "If-Match": str(grant.version)}
    )
    assert response.status_code == 200, response.text
    [row] = _inbox(db_session, anna)
    assert row.event_code == "security.connected_account_revoked"
    assert row.link == "/account/connected-accounts" and row.subject_id == grant.id


@pytest.mark.parametrize(
    ("kind", "details", "actor_is_user", "expected"),
    [
        (AuthEventKind.MFA_ENROLLED, {"factor": "passkey"}, True, "security.factor_enrolled"),
        (AuthEventKind.MFA_REMOVED, {"factors": ["totp"], "all": True}, False, "security.factor_removed"),
        (AuthEventKind.PASSWORD_SET, {"replaced": True}, True, "security.password_changed"),
        (AuthEventKind.PASSWORD_SET, {"replaced": False}, True, None),
        (AuthEventKind.SESSION_REVOKED, {"count": 1, "reason": "logout"}, True, None),
        (
            AuthEventKind.SESSION_REVOKED,
            {"count": 2, "reason": "suspended"},
            False,
            "security.session_revoked_by_other",
        ),
        (AuthEventKind.LOGIN_SUCCEEDED, {"amr": ["pwd"], "newDevice": False}, True, None),
        (AuthEventKind.LOGIN_FAILED, {"reason": "password"}, True, None),
        (AuthEventKind.EMAIL_CHANGED, {}, False, None),
    ],
)
def test_each_auth_event_owes_its_notification(client, db_session, kind, details, actor_is_user, expected):
    anna = create_user(client, create_dealership(client))
    user_id = uuid.UUID(anna["id"])
    auth_events.record(
        db_session,
        kind,
        user_id=user_id,
        dealership_id=uuid.UUID(anna["dealershipId"]),
        actor_id=user_id if actor_is_user else uuid.uuid4(),
        details=details,
    )
    db_session.commit()
    assert _codes(db_session, anna) == ([expected] if expected else [])


def test_the_family_is_complete_for_every_platform_recovery_kind():
    from app.platform.services.security_alerts import RECOVERY_ACTIONS

    assert set(RECOVERY_ACTIONS) == {k.value for k in AuthEventKind if k.value.startswith("recovery_")} - {
        "recovery_code_used",
        "recovery_codes_regenerated",
    }


def _live_session(db_session, user: dict) -> AuthSession:
    db_session.expire_all()
    return db_session.scalars(
        select(AuthSession)
        .where(AuthSession.user_id == uuid.UUID(user["id"]), AuthSession.revoked_at.is_(None))
        .order_by(AuthSession.created_at.desc())
    ).first()


def test_the_bells_poll_does_not_keep_a_session_alive(client, login, db_session):
    """FR-AU-19: an unattended tab must reach its idle end. The bell polls
    the unread count every minute; marked as a background request, the poll
    is checked but does not extend the session — a normal request does."""

    anna, cookie = _user_row(client, login)
    row = _live_session(db_session, anna)
    now = dt.datetime.now(dt.UTC)
    row.last_seen_at = now - dt.timedelta(minutes=30)
    row.idle_expires_at = now + dt.timedelta(minutes=5)
    db_session.commit()
    before = (row.last_seen_at, row.idle_expires_at)

    poll = {**cookie_headers(cookie), "X-DMS-Background": "poll"}
    assert client.get("/v1/me/notifications/unread-count", headers=poll).status_code == 200
    row = _live_session(db_session, anna)
    assert (row.last_seen_at, row.idle_expires_at) == before

    assert client.get("/v1/me/notifications/unread-count", headers=cookie_headers(cookie)).status_code == 200
    row = _live_session(db_session, anna)
    assert row.idle_expires_at > now + dt.timedelta(minutes=100) or row.idle_expires_at == row.absolute_expires_at

    # The poll is still judged: once the session idled out it is refused.
    row.idle_expires_at = now - dt.timedelta(seconds=1)
    db_session.commit()
    assert client.get("/v1/me/notifications/unread-count", headers=poll).status_code == 401


@pytest.mark.parametrize("ended", ["idle", "absolute", "revoked"])
def test_a_session_that_ran_out_gets_no_push_and_loses_its_subscription_daily(client, login, db_session, sender, ended):
    anna, first = _user_row(client, login)
    second = login.session_cookie(anna)
    gone = _subscribe(client, first, "https://push.example.test/send/ended")
    kept = _subscribe(client, second, "https://push.example.test/send/live")
    past = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    column = {"idle": "idle_expires_at", "absolute": "absolute_expires_at", "revoked": "revoked_at"}[ended]
    # Ended without passing auth_sessions (a revoke through it deletes the row at once).
    db_session.execute(
        AuthSession.__table__.update().where(AuthSession.id == uuid.UUID(gone["sessionId"])).values(**{column: past})
    )
    db_session.commit()

    row = notify(db_session, NotificationEventCode.ACCOUNT_LOCKED, user_id=uuid.UUID(anna["id"]), subject=_SUBJECT)
    db_session.commit()
    consumers.handle_notification_delivered_message(db_session, _delivered_message(db_session, row))
    assert [target.endpoint for target, _ in sender.sent] == ["https://push.example.test/send/live"]

    from app.platform.daily_jobs import run_daily_notification_purge

    run_daily_notification_purge(db_session)
    db_session.expire_all()
    assert [str(i) for i in db_session.scalars(select(PushSubscription.id)).all()] == [kept["id"]]
    assert push_subscriptions.delete_for_dead_sessions(db_session) == 0


def test_timing_enum_is_what_the_preference_offers():
    assert [t.value for t in NotificationTiming] == ["immediate", "digest", "off"]
    assert {f.value for f in NotificationFamily} == {"security", "administration", "assignment"}
