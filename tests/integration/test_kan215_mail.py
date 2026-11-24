"""KAN-215: contact mail through the dealer's own mailbox — the link flow, the
sender resolution (Anto's D1 A), the send's delivery record, disconnecting and
ending a grant, the Betrieb's export (D2 A), and the fixes F1–F5 of the
2026-11-15 review.

Fake providers only (`tests/mail_fakes.py`; Anto's Q1 C): no real consent flow
runs here, and that the sent mail appears in the mailbox's «Gesendet» is not
verified by any test.
"""

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.connection import ConnectionEnvironment, ConnectionScope, IntegrationConnection
from app.integration.models.mail import (
    DeliveryStatus,
    MailLinkState,
    MessageDelivery,
    SendingIdentity,
    SendingIdentityKind,
    SendingIdentityStatus,
)
from app.integration.models.provider import IntegrationProvider
from app.integration.public import MailAttachment
from app.integration.services import mail_link, mail_sending, resilience
from app.integration.services.data_export import IntegrationExporter
from app.integration.services.mail_sending import send_contact_mail
from app.platform.identity.models.connected_account import (
    ConnectedAccountProvider,
    ConnectedAccountStatus,
    UserConnectedAccount,
)
from app.platform.organisation.models.dealership import DealerGroup
from tests import mail_fakes
from tests.identapi_support import make_user
from tests.inventory.test_inventory_invoicing_gate import _make_dealership
from tests.mail_fakes import (
    CLIENT_SECRET,
    GOOGLE_CLIENT_ID,
    MS_CLIENT_ID,
    SERVICE_ACCOUNT,
    SERVICE_ACCOUNT_KEY,
    query_of,
)
from tests.membership_support import bearer, token
from tests.postgres_only import NEEDS_ITS_OWN_CONNECTION, POSTGRES_SQL

TID = "0b8e6f2a-5d3c-4c1e-9a7b-2f4d6e8a1c3b"
OTHER_TID = "7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
GLOBAL_ADMIN = "62e90394-69f5-4237-9190-012177145e10"
CALLBACK = "/v1/integrations/mail/link-callback"
OWN = "/v1/integrations/mail/own-mailbox"


@pytest.fixture
def fakes(monkeypatch):
    providers, secrets_store = mail_fakes.install(monkeypatch)
    yield providers, secrets_store
    resilience._CIRCUITS.clear()


def _provider(db: Session, code: str, slots: list[str], keys: list[str]) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code=code, category="calendar_mail", display_name=code, auth_type="oauth2",
        required_secret_slots=slots, required_config_keys=keys, capability_codes=["mail_send"], supports_sandbox=False,
    )
    db.add(provider)
    db.flush()
    return provider


@pytest.fixture
def app_registrations(db_session, fakes):
    """Nexotec's two platform-scope app registrations, their secrets in the fake store."""

    _, secrets_store = fakes
    ms = _provider(db_session, "microsoft_365_mail", ["client_secret"], ["clientId"])
    google = _provider(
        db_session, "google_workspace_mail", ["client_secret", "certificate"], ["clientId", "serviceAccountEmail"]
    )
    connections = {}
    for provider, config in (
        (ms, {"clientId": MS_CLIENT_ID}),
        (google, {"clientId": GOOGLE_CLIENT_ID, "serviceAccountEmail": SERVICE_ACCOUNT}),
    ):
        connection = IntegrationConnection(
            provider_id=provider.id, scope=ConnectionScope.PLATFORM, tenant_id=None, display_name=provider.provider_code,
            environment=ConnectionEnvironment.PRODUCTION, config=config, enabled=True,
        )
        db_session.add(connection)
        db_session.flush()
        secrets_store.values[(connection.id, "client_secret")] = CLIENT_SECRET
        connections[provider.provider_code] = connection
    secrets_store.values[(connections["google_workspace_mail"].id, "certificate")] = SERVICE_ACCOUNT_KEY
    db_session.commit()
    return connections


@pytest.fixture
def betrieb(db_session):
    return _make_dealership(db_session)


def _as(user, *, manager: bool = False, tenant_id=None) -> dict[str, str]:
    return bearer(token(tenant_id=tenant_id or user.tenant_id, user_id=user.id, is_dealer_manager=manager))


def _ms_user_claims(oid: str = "ms-oid-anna", email: str = "anna@garage-muster.ch", tid: str = TID) -> dict:
    return {"tid": tid, "oid": oid, "email": email}


def _connect_own_ms(client, providers, user, **claims) -> dict:
    started = client.post(f"{OWN}/links", json={"provider": "microsoft_365_mail"}, headers=_as(user))
    assert started.status_code == 201, started.text
    callback = providers.sign_in(started.json()["authorizeUrl"], claims=_ms_user_claims(**claims))
    response = client.post(CALLBACK, json=callback, headers=_as(user))
    assert response.status_code == 200, response.text
    return response.json()


def _connect_business_ms(client, providers, manager, address="info@garage-muster.ch", tid=TID) -> dict:
    providers.ms_consented.add(tid)
    providers.ms_mailboxes.setdefault(tid, {})[address] = "ms-obj-" + address
    started = client.post(
        f"/v1/dealerships/{manager.tenant_id}/mail/business-mailbox/links",
        json={"provider": "microsoft_365_mail", "mailboxAddress": address},
        headers=_as(manager, manager=True),
    )
    assert started.status_code == 201, started.text
    callback = providers.sign_in(
        started.json()["authorizeUrl"],
        claims={"tid": tid, "oid": "ms-oid-admin", "email": "admin@garage-muster.ch", "wids": [GLOBAL_ADMIN]},
    )
    leg1 = client.post(CALLBACK, json=callback, headers=_as(manager, manager=True))
    assert leg1.status_code == 200, leg1.text
    assert leg1.json()["status"] == "consent_required"
    consent = query_of(leg1.json()["redirectUrl"])
    leg2 = client.post(
        CALLBACK, json={"state": consent["state"], "adminConsent": "True", "tenant": tid},
        headers=_as(manager, manager=True),
    )
    return {"leg2": leg2, "consent_url": leg1.json()["redirectUrl"]}


# -- the user's own mailbox ----------------------------------------------------------


def test_a_user_connects_their_own_microsoft_mailbox_and_contacts_see_it(client, db_session, fakes, app_registrations, betrieb):
    providers, secrets_store = fakes
    anna = make_user(db_session, tenant_id=betrieb.id, email="anna@garage-muster.ch")

    outcome = _connect_own_ms(client, providers, anna)

    assert outcome["status"] == "connected" and outcome["kind"] == "user_mailbox"
    identity = db_session.scalar(select(SendingIdentity))
    assert (identity.kind, identity.mailbox_address, identity.provider_tenant) == (
        SendingIdentityKind.USER_MAILBOX, "anna@garage-muster.ch", TID
    )
    # F4: the grant is platform's connected-account row; the token is in the secrets manager only.
    grant = db_session.get(UserConnectedAccount, identity.connected_account_id)
    assert grant.user_id == anna.id and grant.provider == ConnectedAccountProvider.MICROSOFT_365
    assert grant.secret_ref == f"/integrations/{grant.id}/refresh_token"
    assert secrets_store.values[(grant.id, "refresh_token")].startswith("rt-")
    # Only a hash of the single-use state is stored.
    state_row = db_session.scalar(select(MailLinkState))
    assert len(state_row.state_hash) == 64 and state_row.consumed_at is not None

    own = client.get(OWN, headers=_as(anna)).json()
    assert own["sender"] == {"kind": "user_mailbox", "address": "anna@garage-muster.ch"}
    assert own["senderRefusal"] is None and own["mailbox"]["status"] == "active"


def test_the_pkce_verifier_and_nonce_are_derived_and_never_stored(app_registrations, db_session, betrieb):
    anna = make_user(db_session, tenant_id=betrieb.id)
    url = mail_link.start_user_link(
        db_session, dealership_id=betrieb.id, user_id=anna.id, provider_code=mail_link.MailProviderCode.MICROSOFT_365
    )
    query = query_of(url)
    assert query["code_challenge"] == mail_link.code_challenge(query["state"])
    assert query["nonce"] == mail_link.oidc_nonce(query["state"])
    assert query["code_challenge_method"] == "S256"
    row = db_session.scalar(select(MailLinkState))
    stored = " ".join(str(getattr(row, column.key)) for column in MailLinkState.__table__.columns)
    for secret in (query["state"], mail_link.code_verifier(query["state"]), query["nonce"]):
        assert secret not in stored


@POSTGRES_SQL
def test_a_state_is_single_use_bound_to_its_user_and_expires(client, db_session, fakes, app_registrations, betrieb):
    providers, _ = fakes
    anna = make_user(db_session, tenant_id=betrieb.id, email="anna@garage-muster.ch")
    ben = make_user(db_session, tenant_id=betrieb.id, email="ben@garage-muster.ch")
    started = client.post(f"{OWN}/links", json={"provider": "microsoft_365_mail"}, headers=_as(anna))
    callback = providers.sign_in(started.json()["authorizeUrl"], claims=_ms_user_claims())

    # Another user holding the state is refused like an unknown one.
    refused = client.post(CALLBACK, json=callback, headers=_as(ben))
    assert refused.status_code == 409 and refused.json()["error"]["details"]["reason"] == "link_expired"
    assert client.post(CALLBACK, json=callback, headers=_as(anna)).status_code == 200
    replay = client.post(CALLBACK, json=callback, headers=_as(anna))
    assert replay.status_code == 409 and replay.json()["error"]["details"]["reason"] == "link_expired"

    # An expired state.
    started = client.post(f"{OWN}/links", json={"provider": "google_workspace_mail"}, headers=_as(ben))
    db_session.execute(MailLinkState.__table__.update().values(expires_at=func.now() - text("interval '1 minute'")))
    db_session.commit()
    late = providers.sign_in(started.json()["authorizeUrl"], claims={"sub": "g-ben", "email": "ben@x.ch", "hd": "x.ch",
                                                                     "email_verified": True})
    assert client.post(CALLBACK, json=late, headers=_as(ben)).json()["error"]["details"]["reason"] == "link_expired"


def test_an_id_token_with_another_nonce_or_a_personal_account_is_refused(client, db_session, fakes, app_registrations, betrieb):
    providers, _ = fakes
    anna = make_user(db_session, tenant_id=betrieb.id)
    started = client.post(f"{OWN}/links", json={"provider": "microsoft_365_mail"}, headers=_as(anna))
    callback = providers.sign_in(started.json()["authorizeUrl"], claims={**_ms_user_claims(), "nonce": "forged"})
    refused = client.post(CALLBACK, json=callback, headers=_as(anna))
    assert refused.status_code == 409 and refused.json()["error"]["details"]["reason"] == "id_token_invalid"

    started = client.post(f"{OWN}/links", json={"provider": "microsoft_365_mail"}, headers=_as(anna))
    personal = providers.sign_in(
        started.json()["authorizeUrl"], claims=_ms_user_claims(tid="9188040d-6c67-4c5b-b112-36a304b66dad")
    )
    refused = client.post(CALLBACK, json=personal, headers=_as(anna))
    assert refused.json()["error"]["details"]["reason"] == "not_a_business_account"
    assert db_session.scalar(select(func.count()).select_from(SendingIdentity)) == 0
    assert db_session.scalar(select(func.count()).select_from(UserConnectedAccount)) == 0


def test_a_provider_never_set_up_by_nexotec_is_refused_before_any_redirect(client, db_session, fakes, betrieb):
    anna = make_user(db_session, tenant_id=betrieb.id)
    refused = client.post(f"{OWN}/links", json={"provider": "microsoft_365_mail"}, headers=_as(anna))
    assert refused.status_code == 409 and refused.json()["error"]["details"]["reason"] == "mail_provider_not_configured"


# -- F1: the shared mailbox needs a verified administrator of its own tenant ----------


def test_a_shared_microsoft_mailbox_is_connected_through_a_verified_admin_and_consent(client, db_session, fakes, app_registrations, betrieb):
    providers, _ = fakes
    manager = make_user(db_session, tenant_id=betrieb.id, email="chef@garage-muster.ch")

    result = _connect_business_ms(client, providers, manager)

    assert result["leg2"].status_code == 200, result["leg2"].text
    assert f"/{TID}/v2.0/adminconsent" in result["consent_url"]
    identity = db_session.scalar(select(SendingIdentity))
    assert (identity.kind, identity.mailbox_address, identity.provider_tenant, identity.connected_account_id) == (
        SendingIdentityKind.BUSINESS_MAILBOX, "info@garage-muster.ch", TID, None
    )
    read = client.get(f"/v1/dealerships/{betrieb.id}/mail/business-mailbox", headers=_as(manager, manager=True)).json()
    assert read["mailbox"]["mailboxAddress"] == "info@garage-muster.ch"


def test_a_signed_in_user_without_a_consent_role_cannot_connect_the_shared_mailbox(client, db_session, fakes, app_registrations, betrieb):
    providers, _ = fakes
    manager = make_user(db_session, tenant_id=betrieb.id)
    started = client.post(
        f"/v1/dealerships/{betrieb.id}/mail/business-mailbox/links",
        json={"provider": "microsoft_365_mail", "mailboxAddress": "info@garage-muster.ch"},
        headers=_as(manager, manager=True),
    )
    callback = providers.sign_in(started.json()["authorizeUrl"], claims={"tid": TID, "oid": "x", "wids": []})
    refused = client.post(CALLBACK, json=callback, headers=_as(manager, manager=True))
    assert refused.status_code == 409 and refused.json()["error"]["details"]["reason"] == "not_an_administrator"


def test_the_consent_leg_never_trusts_the_browsers_tenant(client, db_session, fakes, app_registrations, betrieb):
    """The reviewer's probe: an admin of tenant A signs in, and the browser comes
    back from the consent screen naming tenant B (one that consented to
    Nexotec's app for another dealer)."""

    providers, _ = fakes
    manager = make_user(db_session, tenant_id=betrieb.id)
    providers.ms_consented.update({TID, OTHER_TID})
    providers.ms_mailboxes[OTHER_TID] = {"info@other-dealer.ch": "obj-other"}
    started = client.post(
        f"/v1/dealerships/{betrieb.id}/mail/business-mailbox/links",
        json={"provider": "microsoft_365_mail", "mailboxAddress": "info@other-dealer.ch"},
        headers=_as(manager, manager=True),
    )
    callback = providers.sign_in(started.json()["authorizeUrl"], claims={"tid": TID, "oid": "x", "wids": [GLOBAL_ADMIN]})
    leg1 = client.post(CALLBACK, json=callback, headers=_as(manager, manager=True)).json()
    consent = query_of(leg1["redirectUrl"])
    refused = client.post(
        CALLBACK, json={"state": consent["state"], "adminConsent": "True", "tenant": OTHER_TID},
        headers=_as(manager, manager=True),
    )
    assert refused.status_code == 409 and refused.json()["error"]["details"]["reason"] == "consent_not_granted"
    # And in its own tenant the other dealer's mailbox is not found.
    started = client.post(
        f"/v1/dealerships/{betrieb.id}/mail/business-mailbox/links",
        json={"provider": "microsoft_365_mail", "mailboxAddress": "info@other-dealer.ch"},
        headers=_as(manager, manager=True),
    )
    callback = providers.sign_in(started.json()["authorizeUrl"], claims={"tid": TID, "oid": "x", "wids": [GLOBAL_ADMIN]})
    consent = query_of(client.post(CALLBACK, json=callback, headers=_as(manager, manager=True)).json()["redirectUrl"])
    refused = client.post(
        CALLBACK, json={"state": consent["state"], "adminConsent": "True", "tenant": TID},
        headers=_as(manager, manager=True),
    )
    assert refused.json()["error"]["details"]["reason"] == "mailbox_not_in_tenant"
    assert db_session.scalar(select(func.count()).select_from(SendingIdentity)) == 0


def test_a_tenant_bound_to_a_betrieb_of_another_dealer_group_is_refused(client, db_session, fakes, app_registrations, betrieb):
    providers, _ = fakes
    first = make_user(db_session, tenant_id=betrieb.id, email="chef@garage-muster.ch")
    assert _connect_business_ms(client, providers, first)["leg2"].status_code == 200

    stranger_betrieb = _make_dealership(db_session)
    stranger = make_user(db_session, tenant_id=stranger_betrieb.id, email="chef@stranger.ch")
    started = client.post(
        f"/v1/dealerships/{stranger_betrieb.id}/mail/business-mailbox/links",
        json={"provider": "microsoft_365_mail", "mailboxAddress": "sales@garage-muster.ch"},
        headers=_as(stranger, manager=True),
    )
    callback = providers.sign_in(started.json()["authorizeUrl"], claims={"tid": TID, "oid": "x", "wids": [GLOBAL_ADMIN]})
    refused = client.post(CALLBACK, json=callback, headers=_as(stranger, manager=True))
    assert refused.status_code == 409 and refused.json()["error"]["details"]["reason"] == "tenant_bound_elsewhere"
    assert db_session.scalar(select(func.count()).select_from(SendingIdentity)) == 1


def test_a_sister_betrieb_of_the_same_group_may_bind_the_same_tenant(client, db_session, fakes, app_registrations, betrieb):
    providers, _ = fakes
    first = make_user(db_session, tenant_id=betrieb.id, email="chef@garage-muster.ch")
    assert _connect_business_ms(client, providers, first)["leg2"].status_code == 200
    sister = _make_dealership(db_session)
    sister.dealer_group_id = betrieb.dealer_group_id
    db_session.commit()
    manager = make_user(db_session, tenant_id=sister.id, email="chef@sister.ch")
    assert _connect_business_ms(client, providers, manager, address="sister@garage-muster.ch")["leg2"].status_code == 200


def _google_business_start(client, manager, address):
    return client.post(
        f"/v1/dealerships/{manager.tenant_id}/mail/business-mailbox/links",
        json={"provider": "google_workspace_mail", "mailboxAddress": address},
        headers=_as(manager, manager=True),
    )


def test_a_shared_google_mailbox_needs_an_admin_the_directory_and_the_delegation(client, db_session, fakes, app_registrations, betrieb):
    providers, _ = fakes
    manager = make_user(db_session, tenant_id=betrieb.id)
    providers.google_users["garage-muster.ch"] = {
        "admin@garage-muster.ch": {"id": "g-1", "sub": "g-admin", "isAdmin": True},
        "staff@garage-muster.ch": {"id": "g-2", "sub": "g-staff", "isAdmin": False},
        "info@garage-muster.ch": {"id": "g-3", "sub": "g-info"},
    }

    def attempt(sub: str, email: str, address: str):
        started = _google_business_start(client, manager, address)
        callback = providers.sign_in(
            started.json()["authorizeUrl"],
            claims={"sub": sub, "email": email, "email_verified": True, "hd": "garage-muster.ch"},
        )
        return client.post(CALLBACK, json=callback, headers=_as(manager, manager=True))

    assert attempt("g-staff", "staff@garage-muster.ch", "info@garage-muster.ch").json()["error"]["details"]["reason"] == (
        "not_an_administrator"
    )
    assert attempt("g-admin", "admin@garage-muster.ch", "info@elsewhere.ch").json()["error"]["details"]["reason"] == (
        "mailbox_not_in_tenant"
    )
    assert attempt("g-admin", "admin@garage-muster.ch", "info@garage-muster.ch").json()["error"]["details"]["reason"] == (
        "delegation_not_granted"
    )
    providers.google_delegated.add("info@garage-muster.ch")
    done = attempt("g-admin", "admin@garage-muster.ch", "info@garage-muster.ch")
    assert done.status_code == 200 and done.json()["status"] == "connected"
    identity = db_session.scalar(select(SendingIdentity))
    assert (identity.provider_tenant, identity.mailbox_address) == ("garage-muster.ch", "info@garage-muster.ch")


def test_only_a_manager_starts_the_shared_mailbox_link_and_another_betrieb_is_404(client, db_session, fakes, app_registrations, betrieb):
    salesperson = make_user(db_session, tenant_id=betrieb.id)
    body = {"provider": "microsoft_365_mail", "mailboxAddress": "info@garage-muster.ch"}
    assert client.post(
        f"/v1/dealerships/{betrieb.id}/mail/business-mailbox/links", json=body, headers=_as(salesperson)
    ).status_code == 403
    other = _make_dealership(db_session)
    assert client.get(
        f"/v1/dealerships/{other.id}/mail/business-mailbox", headers=_as(salesperson, manager=True)
    ).status_code == 404


# -- D1 A: whose mailbox the contact sees ---------------------------------------------


def test_the_author_sends_from_their_own_mailbox_else_the_shared_one_else_nothing(client, db_session, fakes, app_registrations, betrieb):
    providers, _ = fakes
    anna = make_user(db_session, tenant_id=betrieb.id, email="anna@garage-muster.ch")
    ben = make_user(db_session, tenant_id=betrieb.id, email="ben@garage-muster.ch")

    refusal = client.get(OWN, headers=_as(anna)).json()
    assert refusal["sender"] is None and refusal["senderRefusal"] == "no_mailbox_connected"
    with pytest.raises(Exception) as caught:
        mail_sending.resolve_sender(db_session, dealership_id=betrieb.id, author_user_id=anna.id)
    assert caught.value.details["ownMailboxScreen"] == "/account/mailbox"
    assert caught.value.details["businessMailboxScreen"] == "/administration/mail"

    _connect_business_ms(client, providers, anna)
    assert client.get(OWN, headers=_as(ben)).json()["sender"] == {
        "kind": "business_mailbox", "address": "info@garage-muster.ch"
    }
    _connect_own_ms(client, providers, ben, oid="oid-ben", email="ben@garage-muster.ch")
    assert client.get(OWN, headers=_as(ben)).json()["sender"]["address"] == "ben@garage-muster.ch"
    assert client.get(OWN, headers=_as(anna)).json()["sender"]["address"] == "info@garage-muster.ch"


def test_an_author_whose_own_mailbox_is_in_error_is_told_so_and_not_sent_from_the_shared_one(client, db_session, fakes, app_registrations, betrieb):
    """F5: never «kein Postfach verbunden» for a mailbox that is connected but
    failing. Anto, 2026-11-18 (D4): refused, never sent from the shared mailbox
    in its place — the author is told to reconnect."""

    providers, _ = fakes
    anna = make_user(db_session, tenant_id=betrieb.id, email="anna@garage-muster.ch")
    _connect_business_ms(client, providers, anna)
    _connect_own_ms(client, providers, anna)
    identity = db_session.scalar(select(SendingIdentity).where(SendingIdentity.kind == SendingIdentityKind.USER_MAILBOX))
    identity.status = SendingIdentityStatus.ERROR
    db_session.commit()

    own = client.get(OWN, headers=_as(anna)).json()
    assert own["senderRefusal"] == "own_mailbox_in_error" and own["mailbox"]["status"] == "error"


def test_a_grant_revoked_in_my_account_counts_as_no_own_mailbox(client, db_session, fakes, app_registrations, betrieb):
    providers, _ = fakes
    anna = make_user(db_session, tenant_id=betrieb.id, email="anna@garage-muster.ch")
    _connect_own_ms(client, providers, anna)
    grant = db_session.scalar(select(UserConnectedAccount))
    revoked = client.delete(f"/v1/me/connected-accounts/{grant.id}", headers={**_as(anna), "If-Match": str(grant.version)})
    assert revoked.status_code == 200, revoked.text
    own = client.get(OWN, headers=_as(anna)).json()
    assert own["mailbox"] is None and own["senderRefusal"] == "no_mailbox_connected"


# -- F2: the send and its delivery record -------------------------------------------


@NEEDS_ITS_OWN_CONNECTION
def test_a_send_writes_one_delivery_row_on_its_own_session_and_leaves_the_callers_alone(client, db_session, engine, fakes, app_registrations, betrieb):
    providers, _ = fakes
    anna = make_user(db_session, tenant_id=betrieb.id, email="anna@garage-muster.ch")
    _connect_own_ms(client, providers, anna)

    with Session(engine) as caller:
        pending = DealerGroup(name="never committed by the send")
        caller.add(pending)
        record = send_contact_mail(
            caller, dealership_id=betrieb.id, author_user_id=anna.id, document_type="offer", document_id=uuid.uuid4(),
            to=["kunde@example.test"], subject="Ihre Offerte", body_text="Guten Tag",
            attachments=[MailAttachment(filename="offerte.pdf", content_type="application/pdf", content=b"%PDF-1.7")],
        )
        caller.rollback()
    assert db_session.scalar(select(func.count()).select_from(DealerGroup).where(DealerGroup.name == pending.name)) == 0

    assert record.status == DeliveryStatus.SENT and record.sender_address == "anna@garage-muster.ch"
    delivery = db_session.get(MessageDelivery, record.delivery_id)
    assert (delivery.tenant_id, delivery.recipients, delivery.subject, delivery.document_type) == (
        betrieb.id, ["kunde@example.test"], "Ihre Offerte", "offer"
    )
    (sent,) = providers.sent
    assert sent.url == "https://graph.microsoft.com/v1.0/me/sendMail"
    assert sent.body["message"]["attachments"][0]["name"] == "offerte.pdf" and sent.body["saveToSentItems"] is True
    operations = set(db_session.scalars(select(IntegrationCallLog.capability)))
    assert {"oauth2_token_refresh", "graph_send_mail"} <= operations


def test_a_provider_failure_is_a_failed_row_and_a_refused_mailbox_goes_to_error_without_the_breaker(client, db_session, fakes, app_registrations, betrieb):
    providers, _ = fakes
    anna = make_user(db_session, tenant_id=betrieb.id, email="anna@garage-muster.ch")
    _connect_own_ms(client, providers, anna)

    def send():
        return send_contact_mail(
            db_session, dealership_id=betrieb.id, author_user_id=anna.id, document_type="offer", document_id=None,
            to=["kunde@example.test"], subject="S", body_text="B",
        )

    providers.fail_sends_with = 503
    failed = send()
    assert (failed.status, failed.failure_code) == (DeliveryStatus.FAILED, "provider_unavailable")

    providers.fail_sends_with = None
    providers.refuse_refresh = True  # the user withdrew the consent at Microsoft
    connection = app_registrations["microsoft_365_mail"]
    refused = send()
    assert (refused.status, refused.failure_code) == (DeliveryStatus.FAILED, "mailbox_refused")
    db_session.expire_all()
    identity = db_session.scalar(select(SendingIdentity))
    assert identity.status == SendingIdentityStatus.ERROR and identity.error_code == "mailbox_refused"
    # The next send says so before anything is written.
    with pytest.raises(ConflictError) as caught:
        send()
    assert caught.value.details["reason"] == "own_mailbox_in_error"
    # Many mailboxes refusing never opens the breaker of Nexotec's one shared connection.
    for _ in range(6):
        identity.status = SendingIdentityStatus.ACTIVE
        db_session.commit()
        assert send().failure_code == "mailbox_refused"
    assert not resilience.is_circuit_open(connection.id)
    statuses = list(db_session.scalars(select(MessageDelivery.status)))
    assert len(statuses) == 8 and set(statuses) == {DeliveryStatus.FAILED}


def test_a_shared_google_mailbox_sends_through_the_delegation(client, db_session, fakes, app_registrations, betrieb):
    providers, _ = fakes
    anna = make_user(db_session, tenant_id=betrieb.id)
    db_session.add(SendingIdentity(
        tenant_id=betrieb.id, kind=SendingIdentityKind.BUSINESS_MAILBOX,
        provider_code=mail_link.MailProviderCode.GOOGLE_WORKSPACE, mailbox_address="info@garage-muster.ch",
        external_account_id="g-3", provider_tenant="garage-muster.ch", connected_at=func.now(),
    ))
    db_session.commit()
    providers.google_delegated.add("info@garage-muster.ch")

    record = send_contact_mail(
        db_session, dealership_id=betrieb.id, author_user_id=anna.id, document_type="offer", document_id=None,
        to=["kunde@example.test"], subject="Ihre Offerte", body_text="Guten Tag",
    )
    assert record.status == DeliveryStatus.SENT
    (sent,) = providers.sent
    assert sent.url.endswith("/gmail/v1/users/me/messages/send") and sent.authorization.startswith("Bearer dwd-")


# -- disconnecting, F3 and F4 --------------------------------------------------------


def test_disconnecting_the_own_mailbox_revokes_platforms_grant_with_its_event(client, db_session, fakes, app_registrations, betrieb):
    providers, _ = fakes
    anna = make_user(db_session, tenant_id=betrieb.id, email="anna@garage-muster.ch")
    _connect_own_ms(client, providers, anna)
    identity = db_session.scalar(select(SendingIdentity))

    gone = client.delete(f"{OWN}/{identity.id}", headers={**_as(anna), "If-Match": str(identity.version)})
    assert gone.status_code == 204, gone.text
    db_session.expire_all()
    grant = db_session.scalar(select(UserConnectedAccount))
    assert grant.status == ConnectedAccountStatus.REVOKED
    assert db_session.scalar(
        select(func.count()).select_from(OutboxMessage).where(OutboxMessage.event_type == "platform.connected_account.revoked")
    ) == 1
    # Another user's mailbox is a 404.
    ben = make_user(db_session, tenant_id=betrieb.id, email="ben@garage-muster.ch")
    assert client.delete(f"{OWN}/{identity.id}", headers={**_as(ben), "If-Match": "1"}).status_code == 404


def _google_user_identity(db_session, *, tenant_id, user, external="g-shared", status=ConnectedAccountStatus.ACTIVE):
    grant = UserConnectedAccount(
        user_id=user.id, provider=ConnectedAccountProvider.GOOGLE_WORKSPACE, external_account_id=external,
        scopes=[], status=status, linked_at=func.now(),
    )
    db_session.add(grant)
    db_session.flush()
    grant.secret_ref = f"/integrations/{grant.id}/refresh_token"
    identity = SendingIdentity(
        tenant_id=tenant_id, kind=SendingIdentityKind.USER_MAILBOX, owner_user_id=user.id,
        provider_code=mail_link.MailProviderCode.GOOGLE_WORKSPACE, connected_account_id=grant.id,
        mailbox_address="shared@garage-muster.ch", external_account_id=external, provider_tenant="garage-muster.ch",
        connected_at=func.now(),
    )
    db_session.add(identity)
    db_session.commit()
    return grant, identity


def test_googles_grant_is_not_revoked_while_another_live_identity_holds_the_account(engine, db_session, fakes, app_registrations, betrieb):
    """F3: Google's revoke ends the client's whole grant for the account."""

    from sqlalchemy.orm import sessionmaker

    from app.platform.public import RevokedGrant

    providers, secrets_store = fakes
    anna = make_user(db_session, tenant_id=betrieb.id, email="anna@garage-muster.ch")
    ben = make_user(db_session, tenant_id=betrieb.id, email="ben@garage-muster.ch")
    first, _ = _google_user_identity(db_session, tenant_id=betrieb.id, user=anna)
    second, _ = _google_user_identity(db_session, tenant_id=betrieb.id, user=ben)
    for grant, refresh in ((first, "rt-first"), (second, "rt-second")):
        secrets_store.values[(grant.id, "refresh_token")] = refresh
        providers.refresh_tokens[refresh] = "at-x"
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def end(grant):
        grant.status = ConnectedAccountStatus.REVOKED
        db_session.commit()
        mail_sending.end_mail_grant(
            RevokedGrant(connected_account_id=grant.id, provider=grant.provider,
                         external_account_id=grant.external_account_id, secret_ref=grant.secret_ref),
            session_factory=factory,
        )

    end(first)
    assert providers.revoked == []  # ben still sends through the same Google account
    assert (first.id, "refresh_token") not in secrets_store.values
    end(second)
    assert providers.revoked == ["rt-second"]
    assert (second.id, "refresh_token") not in secrets_store.values
    db_session.expire_all()
    assert set(db_session.scalars(select(SendingIdentity.status))) == {SendingIdentityStatus.DISCONNECTED}
    # Ending an ended grant again succeeds (the outbox delivers at least once).
    mail_sending.end_mail_grant(
        RevokedGrant(connected_account_id=second.id, provider=second.provider,
                     external_account_id=second.external_account_id, secret_ref=None),
        session_factory=factory,
    )


def test_disconnecting_one_betrieb_keeps_the_grant_a_sister_still_uses(client, db_session, fakes, app_registrations, betrieb):
    providers, _ = fakes
    anna = make_user(db_session, tenant_id=betrieb.id, email="anna@garage-muster.ch")
    sister = _make_dealership(db_session)
    _connect_own_ms(client, providers, anna)
    started = client.post(f"{OWN}/links", json={"provider": "microsoft_365_mail"}, headers=_as(anna, tenant_id=sister.id))
    callback = providers.sign_in(started.json()["authorizeUrl"], claims=_ms_user_claims())
    assert client.post(CALLBACK, json=callback, headers=_as(anna, tenant_id=sister.id)).status_code == 200
    identities = list(db_session.scalars(select(SendingIdentity)))
    assert len({identity.connected_account_id for identity in identities}) == 1  # one grant, re-linked

    home = next(identity for identity in identities if identity.tenant_id == betrieb.id)
    client.delete(f"{OWN}/{home.id}", headers={**_as(anna), "If-Match": str(home.version)})
    db_session.expire_all()
    assert db_session.scalar(select(UserConnectedAccount.status)) == ConnectedAccountStatus.ACTIVE


# -- F5 ---------------------------------------------------------------------------------


def test_a_second_connect_racing_the_first_is_a_409_and_its_new_grant_is_compensated(client, db_session, fakes, app_registrations, betrieb, monkeypatch):
    providers, _ = fakes
    anna = make_user(db_session, tenant_id=betrieb.id, email="anna@garage-muster.ch")
    first = client.post(f"{OWN}/links", json={"provider": "microsoft_365_mail"}, headers=_as(anna))
    second = client.post(f"{OWN}/links", json={"provider": "google_workspace_mail"}, headers=_as(anna))
    assert client.post(CALLBACK, json=providers.sign_in(first.json()["authorizeUrl"], claims=_ms_user_claims()),
                       headers=_as(anna)).status_code == 200
    # The second callback passed the early check before the first committed: the index decides.
    monkeypatch.setattr(mail_link, "live_user_identity", lambda db, **kwargs: None)
    racing = client.post(
        CALLBACK,
        json=providers.sign_in(second.json()["authorizeUrl"], claims={
            "sub": "g-anna", "email": "anna@garage-muster.ch", "email_verified": True, "hd": "garage-muster.ch"
        }),
        headers=_as(anna),
    )
    assert racing.status_code == 409 and racing.json()["error"]["details"]["reason"] == "mailbox_already_connected"
    db_session.expire_all()
    google = db_session.scalar(
        select(UserConnectedAccount).where(UserConnectedAccount.provider == ConnectedAccountProvider.GOOGLE_WORKSPACE)
    )
    assert google.status == ConnectedAccountStatus.REVOKED  # the compensation
    assert db_session.scalar(select(func.count()).select_from(SendingIdentity)) == 1


def test_a_connect_while_one_is_live_is_refused_at_the_start(client, db_session, fakes, app_registrations, betrieb):
    providers, _ = fakes
    anna = make_user(db_session, tenant_id=betrieb.id)
    _connect_own_ms(client, providers, anna)
    again = client.post(f"{OWN}/links", json={"provider": "google_workspace_mail"}, headers=_as(anna))
    assert again.status_code == 409 and again.json()["error"]["details"]["reason"] == "mailbox_already_connected"


def test_a_betrieb_cannot_hold_a_mailbox_provider_connection_of_its_own(client, db_session, app_registrations, betrieb):
    provider = db_session.scalar(select(IntegrationProvider).where(IntegrationProvider.provider_code == "microsoft_365_mail"))
    response = client.post(
        "/v1/integrations/connections",
        json={"providerId": str(provider.id), "displayName": "Unser Postfach", "environment": "production",
              "config": {"clientId": "x"}},
        headers=bearer(token(tenant_id=betrieb.id, is_dealer_manager=True)),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "mail_provider_platform_only"


# -- D2 A: the Betrieb's export ---------------------------------------------------------


def test_the_export_holds_the_shared_mailbox_and_deliveries_but_no_colleagues_mailbox_row(client, db_session, fakes, app_registrations, betrieb):
    from app.core.export import ExportScope

    providers, _ = fakes
    anna = make_user(db_session, tenant_id=betrieb.id, email="anna@garage-muster.ch")
    _connect_business_ms(client, providers, anna)
    _connect_own_ms(client, providers, anna)
    send_contact_mail(
        db_session, dealership_id=betrieb.id, author_user_id=anna.id, document_type="offer", document_id=None,
        to=["kunde@example.test"], subject="Ihre Offerte", body_text="B",
    )
    tables = {exported.table.name: exported for exported in IntegrationExporter().exported_tables()}
    scope = ExportScope(dealership_id=betrieb.id, group_id=betrieb.dealer_group_id)
    identities = tables["integration_sending_identity"]
    rows = db_session.execute(select(identities.table).where(identities.scope(scope))).all()
    assert [row.kind for row in rows] == ["business_mailbox"]
    deliveries = tables["integration_message_delivery"]
    assert deliveries.in_archive is not False
    (delivery,) = db_session.execute(select(deliveries.table).where(deliveries.scope(scope))).all()
    assert (delivery.subject, delivery.recipients, delivery.status) == ("Ihre Offerte", ["kunde@example.test"], "sent")
    # What the contact saw as sender: here the author's own mailbox. Anto,
    # 2026-11-18 (D3): the delivery keeps it — the proof of who sent what; only
    # the list of connected mailboxes leaves a colleague's address out (D2 A).
    assert delivery.sender_address == "anna@garage-muster.ch"
    assert tables["integration_mail_link_state"].in_archive is False


def test_offboarding_ends_every_mailbox_of_the_betrieb_and_frees_its_tenant(client, db_session, fakes, app_registrations, betrieb):
    """Review finding (2026-11-18): an offboarded Betrieb's shared mailbox must
    not stay live — nothing is sent from it again, and the dealer's tenant is
    no longer bound to the offboarded group (KAN-218's «disables every
    connection»). A redelivery changes nothing."""

    from app.core.audit_model import AuditEvent
    from app.core.outbox_model import OutboxMessage
    from app.integration.consumers import handle_dealership_offboarded_message
    from app.integration.services.mail_sending import disconnect_offboarded_dealership_mailboxes
    from app.platform.administration.services import offboarding

    providers, _ = fakes
    anna = make_user(db_session, tenant_id=betrieb.id, email="anna@garage-muster.ch")
    assert _connect_business_ms(client, providers, anna)["leg2"].status_code == 200
    _connect_own_ms(client, providers, anna)

    # Driven through the real consumer and the producer's own row, as the worker runs it.
    offboarding.publish_offboarded(db_session, dealership=betrieb, offboarded_at=offboarding.utcnow())
    db_session.commit()
    message = db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == offboarding.OFFBOARDED_EVENT_TYPE, OutboxMessage.aggregate_id == betrieb.id
        )
    ).one()
    handle_dealership_offboarded_message(db_session, message)
    db_session.commit()
    reasons = set(
        db_session.scalars(
            select(AuditEvent.reason).where(
                AuditEvent.tenant_id == betrieb.id, AuditEvent.action == "mailbox_disconnected"
            )
        )
    )
    assert reasons == {"dealership_offboarded"}
    statuses = set(db_session.scalars(select(SendingIdentity.status).where(SendingIdentity.tenant_id == betrieb.id)))
    assert statuses == {SendingIdentityStatus.DISCONNECTED}
    assert disconnect_offboarded_dealership_mailboxes(db_session, tenant_id=betrieb.id) == 0

    elsewhere = _make_dealership(db_session)
    chef = make_user(db_session, tenant_id=elsewhere.id, email="chef@elsewhere.ch")
    assert _connect_business_ms(client, providers, chef)["leg2"].status_code == 200


def test_linking_records_an_auth_event_naming_the_provider_never_the_address(client, db_session, fakes, app_registrations, betrieb):
    from app.platform.identity.models.auth_event import AuthEvent, AuthEventKind

    providers, _ = fakes
    anna = make_user(db_session, tenant_id=betrieb.id, email="anna@garage-muster.ch")
    _connect_own_ms(client, providers, anna)
    (event,) = db_session.scalars(select(AuthEvent).where(AuthEvent.kind == AuthEventKind.CONNECTED_ACCOUNT_LINKED))
    assert event.user_id == anna.id and event.details["provider"] == "microsoft_365"
    assert "anna@garage-muster.ch" not in str(event.details)


def test_the_worker_registers_the_mailbox_revokers_for_both_providers(engine, monkeypatch):
    import inspect

    from sqlalchemy.orm import sessionmaker

    from app import worker
    from app.integration.public import register_mail_grant_revokers
    from app.platform.identity.services import connected_account as connected_account_service

    registered: dict = {}
    monkeypatch.setattr(connected_account_service, "_REVOKERS", registered)
    register_mail_grant_revokers(sessionmaker(bind=engine))
    assert set(registered) == {ConnectedAccountProvider.MICROSOFT_365, ConnectedAccountProvider.GOOGLE_WORKSPACE}
    # In the worker's composition root, where platform's consumer runs (ADR-118).
    assert "register_mail_grant_revokers(SessionLocal)" in inspect.getsource(worker.run)


def test_kan767_a_mail_providers_rows_carry_its_http_status_and_oauth_code(client, db_session, fakes, app_registrations, betrieb):
    """KAN-767: every attempt's row carries the provider's HTTP status; a
    refused grant's row its OAuth `error` code; a Graph error body (a dict
    under `error`) leaves no identifier."""

    providers, _ = fakes
    anna = make_user(db_session, tenant_id=betrieb.id, email="anna@garage-muster.ch")
    _connect_own_ms(client, providers, anna)
    connection = app_registrations["microsoft_365_mail"]

    def send():
        return send_contact_mail(
            db_session, dealership_id=betrieb.id, author_user_id=anna.id, document_type="offer", document_id=None,
            to=["kunde@example.test"], subject="S", body_text="B",
        )

    def rows():
        db_session.expire_all()
        return list(db_session.scalars(
            select(IntegrationCallLog).where(IntegrationCallLog.connection_id == connection.id)
            .order_by(IntegrationCallLog.created_at, IntegrationCallLog.id)
        ))

    assert send().status == DeliveryStatus.SENT
    sent_row = rows()[-1]
    assert sent_row.capability == "graph_send_mail" and sent_row.provider_error is None
    assert sent_row.http_status is not None and 200 <= sent_row.http_status < 300

    providers.fail_sends_with = 503
    send()
    failed = rows()[-1]
    assert (failed.capability, failed.http_status, failed.provider_error) == ("graph_send_mail", 503, None)

    providers.fail_sends_with = None
    providers.refuse_refresh = True
    send()
    refused = [r for r in rows() if r.http_status == 400]
    assert refused and refused[-1].provider_error == "invalid_grant"
    assert all(r.purpose for r in rows())
