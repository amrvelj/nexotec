"""KAN-196 test support: the password front door as the tests use it.

A user is created through `POST /v1/dealerships/{id}/users`, which mails an
invitation (the `mailbox` fixture records it); accepting the invitation sets
the first password and activates the user; `POST /v1/auth/login` then mints
a session. `PasswordLogin` keeps the password it set per user so a test can
sign the same user in twice.
"""

import dataclasses
import re
import uuid

from fastapi.testclient import TestClient

from app.core.auth import AccessRole, create_access_token
from app.platform.services.mailer import Mail
from tests.dealership_support import valid_uid

STRONG_PASSWORD = "correct-horse-battery-staple-2026"
_LINK = re.compile(r"https?://\S+[?&]token=([A-Za-z0-9_\-]+)")


@dataclasses.dataclass
class RecordingMailer:
    sent: list[Mail] = dataclasses.field(default_factory=list)

    def send(self, mail: Mail) -> None:
        self.sent.append(mail)


def token_in(mail: Mail) -> str:
    match = _LINK.search(mail.text)
    assert match, mail.text
    return match.group(1)


def last_mail_to(mailbox: list[Mail], email: str) -> Mail:
    mails = [m for m in mailbox if m.to == email]
    assert mails, f"no mail to {email}; got {[m.to for m in mailbox]}"
    return mails[-1]


class PasswordLogin:
    """Activates users through their invitation and signs them in."""

    def __init__(self, client: TestClient, mailbox: list[Mail]) -> None:
        self.client = client
        self.mailbox = mailbox
        self.passwords: dict[str, str] = {}

    def accept_invitation(self, user: dict, password: str = STRONG_PASSWORD):
        token = token_in(last_mail_to(self.mailbox, user["email"]))
        response = self.client.post("/v1/auth/invitations/accept", json={"token": token, "password": password})
        assert response.status_code == 200, response.text
        self.passwords[user["email"]] = password
        # Accepting signs the user in; the tests count the sessions they open
        # themselves, so this one is closed again.
        cookie = response.cookies.get("dms_session")
        assert self.client.post("/v1/auth/logout", headers={"Cookie": f"dms_session={cookie}"}).status_code == 200
        return response

    def login(self, user: dict, password: str | None = None):
        if user["email"] not in self.passwords and password is None:
            self.accept_invitation(user)
        pw = password if password is not None else self.passwords[user["email"]]
        return self.client.post("/v1/auth/login", json={"email": user["email"], "password": pw})

    def session_cookie(self, user: dict, password: str | None = None) -> str:
        response = self.login(user, password)
        assert response.status_code == 200, response.text
        assert response.json()["mfaRequired"] is False, response.text
        token = response.cookies.get("dms_session")
        assert token is not None
        return token


def session_cookie_for(client: TestClient, mailbox: list[Mail], user: dict) -> str:
    """One-shot for a freshly invited user: accept the invitation and sign
    in; returns the raw session cookie value. A test that signs the same
    user in twice keeps one `PasswordLogin` instead."""

    return PasswordLogin(client, mailbox).session_cookie(user)


def new_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.ch"


def platform_admin_headers(
    *, tenant_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None, mfa: bool = True
) -> dict[str, str]:
    """A platform staff token. Minted with a second factor by default — the
    shape every real staff session has (D-A-36); `mfa=False` is the
    password-only session the recovery endpoints refuse."""

    tid = tenant_id or uuid.uuid4()
    token = create_access_token(
        user_id=user_id or uuid.uuid4(),
        tenant_id=tid,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({AccessRole.PLATFORM_ADMIN}),
        amr=("pwd", "otp", "mfa") if mfa else ("pwd",),
        mfa=mfa,
    )
    return {"Authorization": f"Bearer {token}"}


def set_mfa_enforced(client: TestClient, dealership_id: str, enforced: bool) -> None:
    """The Betrieb's switch (D-A-09). A password-only session in an
    enforcing Betrieb is confined to enrolment by the session guard, so the
    tests that are not about enrolment switch it off."""

    current = client.get(f"/v1/dealerships/{dealership_id}/auth-config", headers=platform_admin_headers())
    assert current.status_code == 200, current.text
    if current.json()["mfaEnforced"] == enforced:
        return
    response = client.put(
        f"/v1/dealerships/{dealership_id}/auth-config",
        json={"mfaEnforced": enforced},
        headers={**platform_admin_headers(), "If-Match": str(current.json()["version"])},
    )
    assert response.status_code == 200, response.text


def create_dealership(client: TestClient, *, mfa_enforced: bool = False, **overrides) -> str:
    """A Betrieb with MFA enforcement OFF unless asked — see set_mfa_enforced."""

    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "franchiseType": "independent",
        "address": {
            "street": "Bahnhofstrasse",
            "houseNumber": "1",
            "postalCode": "8001",
            "locality": "Zürich",
            "canton": "ZH",
        },
        "phone": "+41441234567",
        "uid": valid_uid(),
    }
    payload.update(overrides)
    response = client.post("/v1/dealerships", json=payload, headers=platform_admin_headers())
    assert response.status_code == 201, response.text
    if not mfa_enforced:
        set_mfa_enforced(client, response.json()["id"], False)
    return response.json()["id"]


def create_user(client: TestClient, dealership_id: str, **overrides) -> dict:
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "email": new_email("anna"),
        "role": "sales",
        "accessRoles": ["sales"],
        "isDealerManager": True,
    }
    payload.update(overrides)
    response = client.post(f"/v1/dealerships/{dealership_id}/users", json=payload, headers=platform_admin_headers())
    assert response.status_code == 201, response.text
    return response.json()


def cookie_headers(token: str) -> dict[str, str]:
    return {"Cookie": f"dms_session={token}"}
