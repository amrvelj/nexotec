"""KAN-201 — My account: the caller's own profile, signature and connected
accounts under `/v1/me` (sessions and MFA stay under `/v1/auth/*`, Anto
2026-10-20), and the manager's read and revoke of a user's connected
accounts (FR-AU-23/24, D-A-15)."""

import datetime as dt
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.object_store import LocalDirectoryObjectStore, get_object_store
from app.main import app as fastapi_app
from app.platform.models.auth_event import AuthEvent, AuthEventKind
from app.platform.models.connected_account import (
    ConnectedAccountProvider,
    ConnectedAccountStatus,
    UserConnectedAccount,
)
from app.platform.services import connected_account
from tests.identity_support import (
    PasswordLogin,
    cookie_headers,
    create_dealership,
    create_user,
    deactivate,
)

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _as(user: dict, *, manager: bool = False, roles: frozenset[AccessRole] = frozenset({AccessRole.SALES})) -> dict:
    tid = uuid.UUID(user["dealershipId"])
    token = create_access_token(
        user_id=uuid.UUID(user["id"]),
        tenant_id=tid,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=roles,
        is_dealer_manager=manager,
    )
    return {"Authorization": f"Bearer {token}"}


def _me(client, user: dict) -> dict:
    response = client.get("/v1/me", headers=_as(user))
    assert response.status_code == 200, response.text
    return response.json()


def _patch_me(client, user: dict, body: dict, version: int):
    return client.patch("/v1/me", json=body, headers={**_as(user), "If-Match": str(version)})


def _grant(db_session, user: dict, *, label: str = "anna@garage.ch") -> UserConnectedAccount:
    row = UserConnectedAccount(
        user_id=uuid.UUID(user["id"]),
        provider=ConnectedAccountProvider.MICROSOFT_365,
        external_account_id=str(uuid.uuid4()),
        external_account_label=label,
        scopes=["Mail.Send", "Calendars.ReadWrite"],
        secret_ref="infisical://connected-accounts/" + str(uuid.uuid4()),
        status=ConnectedAccountStatus.ACTIVE,
        linked_at=dt.datetime.now(dt.UTC),
    )
    db_session.add(row)
    db_session.commit()
    return row


def _audit(db_session, user: dict, action: str) -> list[AuditEvent]:
    return list(
        db_session.scalars(
            select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(user["id"]), AuditEvent.action == action)
        ).all()
    )


@pytest.fixture()
def object_store(tmp_path: Path):
    store = LocalDirectoryObjectStore(tmp_path / "objects")
    fastapi_app.dependency_overrides[get_object_store] = lambda: store
    try:
        yield store
    finally:
        fastapi_app.dependency_overrides.pop(get_object_store, None)


# --- profile -------------------------------------------------------------------------


def test_my_account_shows_my_own_record_with_the_betriebs_default_language(client):
    dealership = create_dealership(client)
    anna = create_user(client, dealership, phone="+41791234567")

    me = _me(client, anna)

    assert me["id"] == anna["id"] and me["email"] == anna["email"]
    assert me["firstName"] == "Anna" and me["phone"] == "+41791234567"
    assert me["correspondenceLanguage"] is None
    assert me["defaultCorrespondenceLanguage"] == "de"
    assert me["signatureBlock"] is None and me["hasSignatureImage"] is False
    assert me["version"] == anna["version"]


def test_a_user_edits_their_own_name_contact_languages_and_signature_text(client, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership, isDealerManager=False)

    response = _patch_me(
        client,
        anna,
        {
            "firstName": "Anja",
            "lastName": "Muster-Keller",
            "phone": "+41791112233",
            "directDial": "+41441234500",
            "correspondenceLanguage": "fr",
            "signatureBlock": "  Verkaufsberaterin\nGarage Musterbetrieb AG  ",
        },
        anna["version"],
    )

    assert response.status_code == 200, response.text
    me = response.json()
    assert (me["firstName"], me["lastName"]) == ("Anja", "Muster-Keller")
    assert (me["phone"], me["directDial"]) == ("+41791112233", "+41441234500")
    assert me["correspondenceLanguage"] == "fr"
    assert me["signatureBlock"] == "Verkaufsberaterin\nGarage Musterbetrieb AG"
    assert me["version"] == anna["version"] + 1
    [row] = _audit(db_session, anna, "profile_updated")
    assert row.actor_id == uuid.UUID(anna["id"])
    assert row.before == {"first_name": "Anna", "last_name": "Muster", "correspondence_language": None}
    assert row.after["first_name"] == "Anja" and row.after["correspondence_language"] == "fr"
    # Contact details and free text are recorded as changed, without their values.
    assert row.after["changed"] == ["direct_dial", "phone", "signature_block"]
    assert "+41791112233" not in str(row.after)

    cleared = _patch_me(
        client, anna, {"phone": None, "correspondenceLanguage": None, "signatureBlock": "   "}, me["version"]
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["phone"] is None and cleared.json()["correspondenceLanguage"] is None
    assert cleared.json()["signatureBlock"] is None


def test_a_patch_that_changes_nothing_keeps_the_version_and_writes_no_audit(client, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)

    response = _patch_me(client, anna, {"firstName": "Anna"}, anna["version"])

    assert response.status_code == 200, response.text
    assert response.json()["version"] == anna["version"]
    assert _audit(db_session, anna, "profile_updated") == []


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"email": "other@example.ch"}, 422),  # the sign-in channel is never the user's to change
        ({"isDealerManager": True}, 422),  # nor anything the manager owns
        ({"firstName": None}, 422),
        ({"firstName": ""}, 422),
        ({"phone": "079 not a number"}, 422),
        ({"correspondenceLanguage": "es"}, 422),
        ({"signatureBlock": "x" * 1001}, 422),
    ],
)
def test_my_account_refuses_what_is_not_mine_to_change(client, body, status):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)

    response = _patch_me(client, anna, body, anna["version"])

    assert response.status_code == status, response.text
    assert _me(client, anna)["email"] == anna["email"]


def test_my_account_needs_if_match_and_refuses_a_stale_version(client):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)

    missing = client.patch("/v1/me", json={"firstName": "Anja"}, headers=_as(anna))
    stale = _patch_me(client, anna, {"firstName": "Anja"}, anna["version"] + 7)

    assert missing.status_code == 400, missing.text
    assert stale.status_code == 409, stale.text


def test_a_session_that_still_owes_a_factor_cannot_reach_my_account(client, mailbox):
    """The session guard confines it to the enrolment paths (KAN-196); My
    account's other tabs wait until a factor is enrolled."""

    dealership = create_dealership(client, mfa_enforced=True)
    anna = create_user(client, dealership)
    cookie = PasswordLogin(client, mailbox).session_cookie(anna)

    refused = client.get("/v1/me", headers=cookie_headers(cookie))

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["details"]["mfaEnrolmentRequired"] is True


# --- signature ----------------------------------------------------------------------


def test_a_user_sets_reads_and_removes_their_own_signature_image(client, object_store, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership, isDealerManager=False)

    put = client.put(
        "/v1/me/signature",
        content=_PNG,
        headers={**_as(anna), "If-Match": str(anna["version"]), "Content-Type": "image/png"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["hasSignatureImage"] is True

    got = client.get("/v1/me/signature", headers=_as(anna))
    assert got.status_code == 200 and got.content == _PNG and got.headers["content-type"] == "image/png"
    [row] = _audit(db_session, anna, "signature_image_set")
    assert row.actor_id == uuid.UUID(anna["id"])

    removed = client.delete("/v1/me/signature", headers={**_as(anna), "If-Match": str(put.json()["version"])})
    assert removed.status_code == 200, removed.text
    assert removed.json()["hasSignatureImage"] is False
    assert client.get("/v1/me/signature", headers=_as(anna)).status_code == 404


def test_my_signature_upload_refuses_what_is_not_an_image(client, object_store):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)

    response = client.put(
        "/v1/me/signature",
        content=b"GIF89a" + b"\x00" * 16,
        headers={**_as(anna), "If-Match": str(anna["version"]), "Content-Type": "image/png"},
    )

    assert response.status_code == 422, response.text


# --- connected accounts --------------------------------------------------------------


def test_my_connected_accounts_are_listed_without_their_secret_reference(client, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    other = create_user(client, dealership, isDealerManager=False)
    _grant(db_session, anna)
    _grant(db_session, other, label="other@garage.ch")

    response = client.get("/v1/me/connected-accounts", headers=_as(anna))

    assert response.status_code == 200, response.text
    [item] = response.json()["items"]
    assert item["provider"] == "microsoft_365" and item["externalAccountLabel"] == "anna@garage.ch"
    assert item["scopes"] == ["Mail.Send", "Calendars.ReadWrite"] and item["status"] == "active"
    assert "secretRef" not in item and "infisical://" not in response.text
    assert "externalAccountId" not in item


def test_no_connected_account_is_an_empty_list_until_the_outlook_integration_links_one(client):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)

    response = client.get("/v1/me/connected-accounts", headers=_as(anna))

    assert response.status_code == 200 and response.json() == {"items": []}


def test_a_user_revokes_their_own_connected_account_and_it_stays_listed(client, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    grant = _grant(db_session, anna)

    response = client.delete(f"/v1/me/connected-accounts/{grant.id}", headers={**_as(anna), "If-Match": "1"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "revoked" and body["revokedReason"] == "user" and body["revokedAt"] is not None
    assert body["version"] == 2
    [event] = db_session.scalars(
        select(AuthEvent).where(AuthEvent.kind == AuthEventKind.CONNECTED_ACCOUNT_REVOKED)
    ).all()
    assert event.user_id == uuid.UUID(anna["id"]) and event.actor_id == uuid.UUID(anna["id"])
    assert event.details == {"connectedAccountId": str(grant.id), "provider": "microsoft_365", "reason": "user"}
    assert len(_audit(db_session, anna, "connected_account_revoked")) == 1
    [listed] = client.get("/v1/me/connected-accounts", headers=_as(anna)).json()["items"]
    assert listed["status"] == "revoked"

    again = client.delete(f"/v1/me/connected-accounts/{grant.id}", headers={**_as(anna), "If-Match": "2"})
    assert again.status_code == 409 and again.json()["error"]["details"]["reason"] == "already_revoked"


def test_a_user_cannot_revoke_someone_elses_connected_account(client, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    other = create_user(client, dealership, isDealerManager=False)
    grant = _grant(db_session, other)

    response = client.delete(f"/v1/me/connected-accounts/{grant.id}", headers={**_as(anna), "If-Match": "1"})

    assert response.status_code == 404, response.text
    db_session.expire_all()
    assert db_session.get(UserConnectedAccount, grant.id).status == ConnectedAccountStatus.ACTIVE


def test_the_manager_sees_a_users_connected_accounts_and_may_revoke_one(client, db_session):
    dealership = create_dealership(client)
    boss = create_user(client, dealership)
    anna = create_user(client, dealership, isDealerManager=False)
    grant = _grant(db_session, anna)
    path = f"/v1/dealerships/{dealership}/users/{anna['id']}/connected-accounts"

    listed = client.get(path, headers=_as(boss, manager=True))
    assert listed.status_code == 200, listed.text
    [item] = listed.json()["items"]
    # D-A-15: provider, scopes and status — not the address, not the usage stamps, never the reference.
    assert set(item) == {"id", "provider", "scopes", "status", "version"}
    assert "anna@garage.ch" not in listed.text and "infisical://" not in listed.text

    revoked = client.delete(f"{path}/{grant.id}", headers={**_as(boss, manager=True), "If-Match": "1"})
    assert revoked.status_code == 200, revoked.text
    assert set(revoked.json()) == {"id", "provider", "scopes", "status", "version"}
    assert revoked.json()["status"] == "revoked"
    db_session.expire_all()
    row = db_session.get(UserConnectedAccount, grant.id)
    assert row.revoked_by == uuid.UUID(boss["id"]) and row.revoked_reason.value == "manager"
    assert row.secret_ref is not None  # the reference is kept


def test_a_colleague_cannot_read_or_revoke_another_users_connected_accounts(client, db_session):
    dealership = create_dealership(client)
    create_user(client, dealership)
    anna = create_user(client, dealership, isDealerManager=False)
    colleague = create_user(client, dealership, isDealerManager=False)
    grant = _grant(db_session, anna)
    path = f"/v1/dealerships/{dealership}/users/{anna['id']}/connected-accounts"

    assert client.get(path, headers=_as(colleague)).status_code == 403
    assert client.delete(f"{path}/{grant.id}", headers={**_as(colleague), "If-Match": "1"}).status_code == 403


def test_another_betriebs_manager_gets_404_for_a_users_connected_accounts(client, db_session):
    home = create_dealership(client)
    elsewhere = create_dealership(client)
    anna = create_user(client, home, isDealerManager=False)
    stranger = create_user(client, elsewhere)
    _grant(db_session, anna)

    response = client.get(
        f"/v1/dealerships/{elsewhere}/users/{anna['id']}/connected-accounts", headers=_as(stranger, manager=True)
    )

    assert response.status_code == 404, response.text


def test_deactivating_a_user_revokes_every_connected_account(client, db_session):
    dealership = create_dealership(client)
    create_user(client, dealership)
    anna = create_user(client, dealership, isDealerManager=False)
    first = _grant(db_session, anna)
    second = _grant(db_session, anna, label="kalender@garage.ch")

    response = deactivate(client, dealership, anna, if_match=anna["version"])

    assert response.status_code == 200, response.text
    db_session.expire_all()
    for grant in (first, second):
        row = db_session.get(UserConnectedAccount, grant.id)
        assert row.status == ConnectedAccountStatus.REVOKED and row.revoked_reason == "deactivated"
    events = db_session.scalars(
        select(AuthEvent).where(AuthEvent.kind == AuthEventKind.CONNECTED_ACCOUNT_REVOKED)
    ).all()
    assert len(events) == 2


# --- the provider's revoker -----------------------------------------------------------


@pytest.fixture()
def provider_revoker(engine):
    """A registered revoker that reads the row from a session of its own —
    what it sees is what was committed."""

    seen: list[tuple[str, str]] = []

    def _revoker(account: UserConnectedAccount) -> None:
        with Session(engine) as other:
            committed = other.get(UserConnectedAccount, account.id)
            seen.append((str(account.id), committed.status.value))

    connected_account.register_grant_revoker(ConnectedAccountProvider.MICROSOFT_365, _revoker)
    try:
        yield seen
    finally:
        connected_account.unregister_grant_revoker(ConnectedAccountProvider.MICROSOFT_365)


def test_the_providers_revoker_runs_after_the_revoke_committed(client, db_session, provider_revoker):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    grant = _grant(db_session, anna)

    response = client.delete(f"/v1/me/connected-accounts/{grant.id}", headers={**_as(anna), "If-Match": "1"})

    assert response.status_code == 200, response.text
    assert provider_revoker == [(str(grant.id), "revoked")]


def test_a_failing_provider_never_undoes_a_revoke_or_a_deactivation(client, db_session, caplog):
    dealership = create_dealership(client)
    create_user(client, dealership)
    anna = create_user(client, dealership, isDealerManager=False)
    first = _grant(db_session, anna)
    second = _grant(db_session, anna, label="kalender@garage.ch")

    def _down(account: UserConnectedAccount) -> None:
        raise RuntimeError("graph.microsoft.com is down")

    connected_account.register_grant_revoker(ConnectedAccountProvider.MICROSOFT_365, _down)
    try:
        revoked = client.delete(f"/v1/me/connected-accounts/{first.id}", headers={**_as(anna), "If-Match": "1"})
        deactivated = deactivate(client, dealership, anna, if_match=anna["version"])
    finally:
        connected_account.unregister_grant_revoker(ConnectedAccountProvider.MICROSOFT_365)

    assert revoked.status_code == 200, revoked.text
    assert deactivated.status_code == 200, deactivated.text
    db_session.expire_all()
    for grant in (first, second):
        row = db_session.get(UserConnectedAccount, grant.id)
        assert row.status == ConnectedAccountStatus.REVOKED and row.secret_ref is not None
    assert sum("did not end grant" in r.getMessage() for r in caplog.records) == 2


def test_the_same_mailbox_can_be_linked_again_after_a_revoke(client, db_session):
    """A revoked grant keeps its row; only live grants are unique per user,
    provider and account — re-linking is a new row, the old stamps stay."""

    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    first = _grant(db_session, anna)
    assert client.delete(f"/v1/me/connected-accounts/{first.id}", headers={**_as(anna), "If-Match": "1"}).status_code == 200

    again = UserConnectedAccount(
        user_id=first.user_id,
        provider=first.provider,
        external_account_id=first.external_account_id,
        scopes=["Mail.Send"],
        status=ConnectedAccountStatus.ACTIVE,
        linked_at=dt.datetime.now(dt.UTC),
    )
    db_session.add(again)
    db_session.commit()

    twice = UserConnectedAccount(
        user_id=first.user_id,
        provider=first.provider,
        external_account_id=first.external_account_id,
        scopes=["Mail.Send"],
        status=ConnectedAccountStatus.ACTIVE,
        linked_at=dt.datetime.now(dt.UTC),
    )
    db_session.add(twice)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
