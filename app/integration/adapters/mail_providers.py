"""The two mailbox adapters (KAN-215) over `httpx`: Microsoft 365 through the
Microsoft identity platform and Graph, Google Workspace through Google's
OAuth 2.0 / OpenID Connect endpoints, the Admin SDK Directory API and Gmail.

Built and tested against fake providers only (Anto's Q1 C, 2026-11-15): no
Nexotec app is registered at Microsoft or Google yet, so no real consent flow
has run and «the sent mail appears in Gesendet» is unverified. What the code
relies on, and must be confirmed on a real tenant before the first dealer:

- Microsoft: the id_token's `tid`, `oid`, `nonce` and — for a shared mailbox —
  `wids` (the user's directory roles; emitted when the app registration's
  manifest asks for them, `groupMembershipClaims` / optional claim `wids`).
  A shared mailbox may be connected only by a Global Administrator or a
  Privileged Role Administrator: the two roles that can grant tenant-wide
  admin consent to an application permission of Graph.
- Google: the id_token's `sub`, `email`, `email_verified`, `hd` and `nonce`;
  the Directory API's `isAdmin` for the signing-in user; a token minted for the
  shared mailbox through domain-wide delegation proves the delegation.

The client id and secret are those of Nexotec's own app registration: the
platform-scope connection to the provider (config `clientId`, slot
`client_secret`; Google's service account: config `serviceAccountEmail`, slot
`certificate` = its private key). A user's refresh token lives in the secrets
manager under the grant's id; only this module reads it.

Error mapping (the gateway charges the breaker on transport failures only):
- `invalid_client` (Nexotec's own app registration refused) → `ProviderRejectedError`;
- a token endpoint's `invalid_grant` / `unauthorized_client`, a 401 / 403 → `ProviderAccountRefusedError`
  (one mailbox's answer — consent withdrawn, mailbox gone: the identity goes to
  error; never charged to the shared connection's breaker);
- 400 / 404 / 422 → `ProviderRequestError`;
- 5xx, a transport failure → `ProviderTransportError`; a timeout → `ProviderTimeoutError`.
No provider text is copied into an exception: it may name accounts.
"""

import base64
import email.message
import email.utils
import time
import urllib.parse
import uuid
from collections.abc import Callable
from typing import Any, cast

import httpx
import jwt
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.integration.adapters.mail_base import (
    CodeGrant,
    MailboxInfo,
    MailLinkRefused,
    OutgoingMail,
    SentMail,
    VerifiedSignIn,
)
from app.integration.errors import (
    ProviderAccountRefusedError,
    ProviderConfigurationError,
    ProviderRejectedError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from app.integration.models.connection import IntegrationConnection
from app.integration.models.mail import MailProviderCode
from app.integration.models.secret_ref import SecretSlot
from app.integration.services import secrets_backend
from app.integration.services.metering import provider_request

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_JWKS_TTL_S = 3600.0
_CLOCK_SKEW_S = 120

# Microsoft's built-in directory role template ids (stable across tenants).
MS_GLOBAL_ADMINISTRATOR = "62e90394-69f5-4237-9190-012177145e10"
MS_PRIVILEGED_ROLE_ADMINISTRATOR = "e8611ab8-c189-46e8-94e1-60213ab1f814"
MS_CONSENT_ROLES = frozenset({MS_GLOBAL_ADMINISTRATOR, MS_PRIVILEGED_ROLE_ADMINISTRATOR})
# Personal Microsoft accounts sign in under this tenant id: never a business mailbox.
MS_CONSUMER_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"

_MS_LOGIN = "https://login.microsoftonline.com"
_MS_GRAPH = "https://graph.microsoft.com/v1.0"
_MS_DISCOVERY = f"{_MS_LOGIN}/organizations/v2.0/.well-known/openid-configuration"
_MS_USER_SCOPES = ("openid", "profile", "email", "offline_access", "https://graph.microsoft.com/Mail.Send")
_MS_ADMIN_SCOPES = ("openid", "profile", "email")

_GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
_GOOGLE_REVOKE = "https://oauth2.googleapis.com/revoke"
_GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"
_GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})
_GOOGLE_DIRECTORY = "https://admin.googleapis.com/admin/directory/v1/users"
_GOOGLE_GMAIL_SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
_GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
_GOOGLE_USER_SCOPES = ("openid", "email", _GMAIL_SEND_SCOPE)
_GOOGLE_ADMIN_SCOPES = ("openid", "email", "https://www.googleapis.com/auth/admin.directory.user.readonly")

_REFRESH_SLOT = SecretSlot.REFRESH_TOKEN.value

# uri -> (fetched at, keys document). Public keys, not secrets.
_JWKS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def http_transport() -> httpx.BaseTransport | None:
    """The transport every request uses — httpx's own in the application; a
    test replaces this function with a `MockTransport` (the fake providers)."""

    return None


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.TimeoutException, ProviderTimeoutError))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def mime_message(message: OutgoingMail) -> bytes:
    """The RFC 5322 message Gmail's `messages.send` takes (Graph takes JSON)."""

    mime = email.message.EmailMessage()
    mime["From"] = message.sender
    mime["To"] = ", ".join(message.to)
    if message.cc:
        mime["Cc"] = ", ".join(message.cc)
    mime["Subject"] = message.subject
    mime["Date"] = email.utils.formatdate(usegmt=True)
    mime["Message-ID"] = email.utils.make_msgid(domain=message.sender.rpartition("@")[2] or None)
    mime.set_content(message.body_text)
    for attachment in message.attachments:
        maintype, _, subtype = attachment.content_type.partition("/")
        mime.add_attachment(
            attachment.content, maintype=maintype or "application", subtype=subtype or "octet-stream",
            filename=attachment.filename,
        )
    return mime.as_bytes()


class _HttpMailAdapter:
    provider_code: str
    provider_label: str
    needs_admin_consent: bool

    def __init__(
        self, db: Session, connection: IntegrationConnection, actor_id: uuid.UUID | None, purpose: str
    ) -> None:
        self._db = db
        self._connection = connection
        self._actor_id = actor_id
        self._purpose = purpose

    # -- Nexotec's app registration -----------------------------------------

    def _config(self, key: str) -> str:
        value = (self._connection.config or {}).get(key)
        if not isinstance(value, str) or not value.strip():
            raise ProviderConfigurationError(
                f"Nexotec's {self.provider_label} app registration has no '{key}' set."
            )
        return value.strip()

    def _resolve(self, *, folder_id: uuid.UUID, slot: str, what: str) -> str:
        try:
            value = secrets_backend.resolve_secret(connection_id=folder_id, slot=slot)
        except Exception as exc:
            # Class name only: the secrets manager's message may name its path.
            raise ProviderConfigurationError(f"The {what} could not be read ({type(exc).__name__}).") from exc
        record_audit_event(
            self._db,
            entity_type="integration_secret_ref",
            entity_id=folder_id,
            tenant_id=self._connection.tenant_id,
            action="secret_resolved",
            actor_id=self._actor_id,
            reason=f"slot={slot} purpose={self._purpose}",
        )
        return value

    def _client_id(self) -> str:
        return self._config("clientId")

    def _client_secret(self) -> str:
        return self._resolve(
            folder_id=self._connection.id, slot=SecretSlot.CLIENT_SECRET.value, what="app registration's client secret"
        )

    def _refresh_token(self, grant_id: uuid.UUID) -> str:
        return self._resolve(folder_id=grant_id, slot=_REFRESH_SLOT, what="mailbox grant")

    # -- the one choke point -------------------------------------------------

    def _request(
        self,
        operation: str,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        expect: Callable[[httpx.Response], bool] | None = None,
    ) -> httpx.Response:
        """One metered request. `expect` lets a caller accept a status it
        handles itself (a 404 meaning «no such mailbox»)."""

        with provider_request(operation, is_timeout=_is_timeout) as request:
            def _send() -> httpx.Response:
                try:
                    with httpx.Client(timeout=_TIMEOUT, transport=http_transport()) as client:
                        response = client.request(method, url, headers=headers, data=data, json=json_body)
                except httpx.TimeoutException as exc:
                    raise ProviderTimeoutError(f"{self.provider_label} did not answer in time.") from exc
                except httpx.HTTPError as exc:
                    raise ProviderTransportError(
                        f"{self.provider_label} could not be reached ({type(exc).__name__})."
                    ) from exc
                # KAN-767: the status on this attempt's row, and a refusal's
                # OAuth `error` code (never its description).
                request.note_response(
                    response.status_code,
                    provider_error=self._oauth_error(response) if response.status_code >= 400 else None,
                )
                return response

            response = request.attempt(_send)
            if expect is not None and expect(response):
                return response
            self._raise_for_status(response)
            return response

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        oauth_error = self._oauth_error(response)
        if oauth_error == "invalid_client":
            # Nexotec's own app registration is refused: the connection's fault.
            raise ProviderRejectedError(f"{self.provider_label} refused Nexotec's app registration.")
        if status in (401, 403) or (status == 400 and oauth_error in ("invalid_grant", "unauthorized_client")):
            # One mailbox's answer (consent withdrawn, token dead), never the connection's.
            raise ProviderAccountRefusedError(f"{self.provider_label} refused the mailbox's grant (HTTP {status}).")
        if status in (400, 404, 422):
            raise ProviderRequestError(f"{self.provider_label} refused the request (HTTP {status}).")
        raise ProviderTransportError(f"{self.provider_label} failed to answer (HTTP {status}).")

    @staticmethod
    def _oauth_error(response: httpx.Response) -> str | None:
        """The OAuth `error` code alone (`invalid_grant`, `invalid_client`, …)
        — a closed vocabulary, so only a word of letters, digits and
        underscores is read; anything else (prose under `error`, Graph's
        dict-shaped error) is None. It lands on the call-log row (KAN-767),
        which never holds a provider's prose."""

        try:
            body = response.json()
        except ValueError:
            return None
        code = body.get("error") if isinstance(body, dict) else None
        return code if isinstance(code, str) and code.replace("_", "").isalnum() else None

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderTransportError(f"{self.provider_label} sent an answer that is not JSON.") from exc
        if not isinstance(body, dict):
            raise ProviderTransportError(f"{self.provider_label} sent an answer of an unexpected shape.")
        return body

    # -- id_token verification ----------------------------------------------

    def _discovery_url(self) -> str:
        raise NotImplementedError

    def _jwks(self, *, refresh: bool = False) -> dict[str, Any]:
        discovery_url = self._discovery_url()
        cached = _JWKS_CACHE.get(discovery_url)
        if cached is not None and not refresh and time.monotonic() - cached[0] < _JWKS_TTL_S:
            return cached[1]
        discovery = self._json(self._request("openid_configuration", "GET", discovery_url))
        jwks_uri = discovery.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri.startswith("https://"):
            raise ProviderTransportError(f"{self.provider_label} published no key set.")
        keys = self._json(self._request("jwks", "GET", jwks_uri))
        _JWKS_CACHE[discovery_url] = (time.monotonic(), keys)
        return keys

    def _signing_key(self, kid: str) -> Any:
        for refresh in (False, True):  # a rotated key: fetch the set once more
            for key in self._jwks(refresh=refresh).get("keys", []):
                if isinstance(key, dict) and key.get("kid") == kid:
                    return jwt.PyJWK.from_dict(key, algorithm="RS256").key
        raise MailLinkRefused("id_token_invalid")

    def _decode_id_token(self, id_token: str, *, nonce: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise MailLinkRefused("id_token_invalid") from exc
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise MailLinkRefused("id_token_invalid")
        key = self._signing_key(header["kid"])
        try:
            claims = jwt.decode(
                id_token,
                key=key,
                algorithms=["RS256"],
                audience=self._client_id(),
                leeway=_CLOCK_SKEW_S,
                options={"require": ["exp", "iat", "iss", "aud", "sub"], "verify_iss": False},
            )
        except jwt.PyJWTError as exc:
            raise MailLinkRefused("id_token_invalid") from exc
        if claims.get("nonce") != nonce:
            raise MailLinkRefused("id_token_invalid")
        return claims

    def _token_grant(self, body: dict[str, Any]) -> CodeGrant:
        id_token = body.get("id_token")
        if not isinstance(id_token, str):
            raise ProviderTransportError(f"{self.provider_label} sent no id_token.")
        scopes = tuple(str(body.get("scope") or "").split())
        access = body.get("access_token")
        refresh = body.get("refresh_token")
        return CodeGrant(
            id_token=id_token,
            access_token=access if isinstance(access, str) else None,
            refresh_token=refresh if isinstance(refresh, str) else None,
            scopes=scopes,
        )

    def end_user_grant(self, *, grant_id: uuid.UUID) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class MicrosoftMailAdapter(_HttpMailAdapter):
    provider_code = MailProviderCode.MICROSOFT_365.value
    provider_label = "Microsoft 365"
    needs_admin_consent = True

    def _discovery_url(self) -> str:
        return _MS_DISCOVERY

    def authorize_url(
        self, *, admin: bool, state: str, nonce: str, code_challenge: str, redirect_uri: str, login_hint: str | None
    ) -> str:
        params = {
            "client_id": self._client_id(),
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": " ".join(_MS_ADMIN_SCOPES if admin else _MS_USER_SCOPES),
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        if login_hint:
            params["login_hint"] = login_hint
        return f"{_MS_LOGIN}/organizations/oauth2/v2.0/authorize?{urllib.parse.urlencode(params)}"

    def consent_url(self, *, provider_tenant: str, state: str, redirect_uri: str) -> str | None:
        params = {
            "client_id": self._client_id(),
            "scope": "https://graph.microsoft.com/.default",
            "redirect_uri": redirect_uri,
            "state": state,
        }
        tenant = urllib.parse.quote(provider_tenant, safe="")
        return f"{_MS_LOGIN}/{tenant}/v2.0/adminconsent?{urllib.parse.urlencode(params)}"

    def redeem_code(self, *, code: str, code_verifier: str, redirect_uri: str, admin: bool) -> CodeGrant:
        response = self._request(
            "oauth2_token",
            "POST",
            f"{_MS_LOGIN}/organizations/oauth2/v2.0/token",
            data={
                "grant_type": "authorization_code",
                "client_id": self._client_id(),
                "client_secret": self._client_secret(),
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
                "scope": " ".join(_MS_ADMIN_SCOPES if admin else _MS_USER_SCOPES),
            },
        )
        return self._token_grant(self._json(response))

    def verify_sign_in(self, grant: CodeGrant, *, nonce: str) -> VerifiedSignIn:
        claims = self._decode_id_token(grant.id_token, nonce=nonce)
        tid = claims.get("tid")
        oid = claims.get("oid")
        if not isinstance(tid, str) or not isinstance(oid, str) or tid == MS_CONSUMER_TENANT:
            raise MailLinkRefused("not_a_business_account")
        try:
            uuid.UUID(tid)
        except ValueError as exc:
            raise MailLinkRefused("id_token_invalid") from exc
        # Microsoft's issuer names the tenant that signed the user in.
        if claims.get("iss") != f"{_MS_LOGIN}/{tid}/v2.0":
            raise MailLinkRefused("id_token_invalid")
        address = claims.get("email") or claims.get("preferred_username")
        wids = claims.get("wids")
        return VerifiedSignIn(
            subject=oid,
            email=address.lower() if isinstance(address, str) else None,
            provider_tenant=tid,
            admin_roles=tuple(str(role) for role in wids) if isinstance(wids, list) else (),
        )

    def administers_tenant(self, grant: CodeGrant, sign_in: VerifiedSignIn) -> bool:
        return bool(MS_CONSENT_ROLES.intersection(sign_in.admin_roles))

    def _app_token(self, provider_tenant: str) -> str:
        """An application token for the dealer's tenant: granted only once
        the tenant's administrator consented to Nexotec's app."""

        tenant = urllib.parse.quote(provider_tenant, safe="")
        response = self._request(
            "oauth2_token_client_credentials",
            "POST",
            f"{_MS_LOGIN}/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id(),
                "client_secret": self._client_secret(),
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        token = self._json(response).get("access_token")
        if not isinstance(token, str):
            raise ProviderTransportError("Microsoft 365 sent no access token.")
        return token

    def find_business_mailbox(
        self, *, provider_tenant: str, address: str, admin_grant: CodeGrant | None
    ) -> MailboxInfo | None:
        token = self._app_token(provider_tenant)
        query = urllib.parse.urlencode({"$select": "id,mail,userPrincipalName"})
        response = self._request(
            "graph_get_user",
            "GET",
            f"{_MS_GRAPH}/users/{urllib.parse.quote(address, safe='@')}?{query}",
            headers={"Authorization": f"Bearer {token}"},
            expect=lambda r: r.status_code == 404,
        )
        if response.status_code == 404:
            return None
        body = self._json(response)
        known = {str(body.get(key) or "").lower() for key in ("mail", "userPrincipalName")}
        object_id = body.get("id")
        if address.lower() not in known or not isinstance(object_id, str):
            return None
        return MailboxInfo(external_account_id=object_id, address=address.lower())

    @staticmethod
    def _graph_message(message: OutgoingMail) -> dict[str, Any]:
        return {
            "message": {
                "subject": message.subject,
                "body": {"contentType": "Text", "content": message.body_text},
                "toRecipients": [{"emailAddress": {"address": to}} for to in message.to],
                "ccRecipients": [{"emailAddress": {"address": cc}} for cc in message.cc],
                "attachments": [
                    {
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": attachment.filename,
                        "contentType": attachment.content_type,
                        "contentBytes": base64.b64encode(attachment.content).decode("ascii"),
                    }
                    for attachment in message.attachments
                ],
            },
            # The mail lands in the mailbox's «Gesendet» (unverified, Q1 C).
            "saveToSentItems": True,
        }

    def send_as_business(self, *, provider_tenant: str, message: OutgoingMail) -> SentMail:
        token = self._app_token(provider_tenant)
        self._request(
            "graph_send_mail",
            "POST",
            f"{_MS_GRAPH}/users/{urllib.parse.quote(message.sender, safe='@')}/sendMail",
            headers={"Authorization": f"Bearer {token}"},
            json_body=self._graph_message(message),
        )
        return SentMail(provider_message_id=None)  # Graph answers 202 without an id

    def _user_access_token(self, grant_id: uuid.UUID) -> str:
        response = self._request(
            "oauth2_token_refresh",
            "POST",
            f"{_MS_LOGIN}/organizations/oauth2/v2.0/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self._client_id(),
                "client_secret": self._client_secret(),
                "refresh_token": self._refresh_token(grant_id),
                "scope": " ".join(_MS_USER_SCOPES),
            },
        )
        body = self._json(response)
        token = body.get("access_token")
        if not isinstance(token, str):
            raise ProviderTransportError("Microsoft 365 sent no access token.")
        rotated = body.get("refresh_token")
        if isinstance(rotated, str):
            # Microsoft rotates refresh tokens: keep the newest (a replace, never a read).
            secrets_backend.update_secret(connection_id=grant_id, slot=_REFRESH_SLOT, value=rotated)
        return token

    def send_as_user(self, *, grant_id: uuid.UUID, message: OutgoingMail) -> SentMail:
        token = self._user_access_token(grant_id)
        self._request(
            "graph_send_mail",
            "POST",
            f"{_MS_GRAPH}/me/sendMail",
            headers={"Authorization": f"Bearer {token}"},
            json_body=self._graph_message(message),
        )
        return SentMail(provider_message_id=None)

    def end_user_grant(self, *, grant_id: uuid.UUID) -> None:
        """Microsoft has no endpoint that revokes one refresh token (revoking
        the user's sign-in sessions would sign them out everywhere): the
        grant ends when the token is dropped from the secrets manager, which
        the caller does next. Nothing is sent."""

        return


class GoogleMailAdapter(_HttpMailAdapter):
    provider_code = MailProviderCode.GOOGLE_WORKSPACE.value
    provider_label = "Google Workspace"
    needs_admin_consent = False

    def _discovery_url(self) -> str:
        return _GOOGLE_DISCOVERY

    def authorize_url(
        self, *, admin: bool, state: str, nonce: str, code_challenge: str, redirect_uri: str, login_hint: str | None
    ) -> str:
        params = {
            "client_id": self._client_id(),
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(_GOOGLE_ADMIN_SCOPES if admin else _GOOGLE_USER_SCOPES),
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "include_granted_scopes": "false",
        }
        if admin:
            params["prompt"] = "select_account"
        else:
            params["access_type"] = "offline"
            params["prompt"] = "consent"  # a refresh token on every link
        if login_hint:
            params["login_hint"] = login_hint
        return f"{_GOOGLE_AUTH}?{urllib.parse.urlencode(params)}"

    def consent_url(self, *, provider_tenant: str, state: str, redirect_uri: str) -> str | None:
        return None

    def redeem_code(self, *, code: str, code_verifier: str, redirect_uri: str, admin: bool) -> CodeGrant:
        response = self._request(
            "oauth2_token",
            "POST",
            _GOOGLE_TOKEN,
            data={
                "grant_type": "authorization_code",
                "client_id": self._client_id(),
                "client_secret": self._client_secret(),
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
        )
        return self._token_grant(self._json(response))

    def verify_sign_in(self, grant: CodeGrant, *, nonce: str) -> VerifiedSignIn:
        claims = self._decode_id_token(grant.id_token, nonce=nonce)
        if claims.get("iss") not in _GOOGLE_ISSUERS:
            raise MailLinkRefused("id_token_invalid")
        domain = claims.get("hd")
        address = claims.get("email")
        if not isinstance(domain, str) or not domain:
            raise MailLinkRefused("not_a_business_account")  # a consumer Gmail account
        if not isinstance(address, str) or claims.get("email_verified") is not True:
            raise MailLinkRefused("id_token_invalid")
        return VerifiedSignIn(
            subject=str(claims["sub"]), email=address.lower(), provider_tenant=domain.lower(), admin_roles=()
        )

    def _directory_user(self, access_token: str, key: str, fields: str) -> dict[str, Any] | None:
        response = self._request(
            "directory_users_get",
            "GET",
            f"{_GOOGLE_DIRECTORY}/{urllib.parse.quote(key, safe='@')}?{urllib.parse.urlencode({'fields': fields})}",
            headers={"Authorization": f"Bearer {access_token}"},
            expect=lambda r: r.status_code == 404,
        )
        return None if response.status_code == 404 else self._json(response)

    def administers_tenant(self, grant: CodeGrant, sign_in: VerifiedSignIn) -> bool:
        if grant.access_token is None:
            return False
        user = self._directory_user(grant.access_token, sign_in.subject, "isAdmin")
        return bool(user and user.get("isAdmin") is True)

    def _delegated_token(self, mailbox: str) -> str:
        """A token for `mailbox` through domain-wide delegation: Nexotec's
        service account asserts the mailbox as its subject. Refused unless the
        dealer's administrator delegated `gmail.send` to the service account."""

        now = int(time.time())
        private_key = self._resolve(
            folder_id=self._connection.id, slot=SecretSlot.CERTIFICATE.value, what="service account key"
        )
        assertion = jwt.encode(
            {
                "iss": self._config("serviceAccountEmail"),
                "sub": mailbox,
                "scope": _GMAIL_SEND_SCOPE,
                "aud": _GOOGLE_TOKEN,
                "iat": now,
                "exp": now + 600,
            },
            private_key,
            algorithm="RS256",
        )
        response = self._request(
            "oauth2_token_jwt_bearer",
            "POST",
            _GOOGLE_TOKEN,
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
        )
        token = self._json(response).get("access_token")
        if not isinstance(token, str):
            raise ProviderTransportError("Google Workspace sent no access token.")
        return token

    def find_business_mailbox(
        self, *, provider_tenant: str, address: str, admin_grant: CodeGrant | None
    ) -> MailboxInfo | None:
        if admin_grant is None or admin_grant.access_token is None:
            return None
        # The Directory answers only for the signing-in administrator's own
        # organisation: a 200 is the proof the mailbox belongs to it.
        user = self._directory_user(admin_grant.access_token, address, "id,primaryEmail,emails")
        if user is None or not isinstance(user.get("id"), str):
            return None
        known = {str(user.get("primaryEmail") or "").lower()}
        for alias in user.get("emails") or []:
            if isinstance(alias, dict):
                known.add(str(alias.get("address") or "").lower())
        if address.lower() not in known:
            return None
        try:
            self._delegated_token(address.lower())
        except ProviderAccountRefusedError as exc:
            raise MailLinkRefused("delegation_not_granted") from exc
        return MailboxInfo(external_account_id=str(user["id"]), address=address.lower())

    def _gmail_send(self, token: str, message: OutgoingMail) -> SentMail:
        response = self._request(
            "gmail_messages_send",
            "POST",
            _GOOGLE_GMAIL_SEND,
            headers={"Authorization": f"Bearer {token}"},
            json_body={"raw": _b64url(mime_message(message))},
        )
        message_id = self._json(response).get("id")
        return SentMail(provider_message_id=message_id if isinstance(message_id, str) else None)

    def send_as_business(self, *, provider_tenant: str, message: OutgoingMail) -> SentMail:
        return self._gmail_send(self._delegated_token(message.sender), message)

    def send_as_user(self, *, grant_id: uuid.UUID, message: OutgoingMail) -> SentMail:
        response = self._request(
            "oauth2_token_refresh",
            "POST",
            _GOOGLE_TOKEN,
            data={
                "grant_type": "refresh_token",
                "client_id": self._client_id(),
                "client_secret": self._client_secret(),
                "refresh_token": self._refresh_token(grant_id),
            },
        )
        token = self._json(response).get("access_token")
        if not isinstance(token, str):
            raise ProviderTransportError("Google Workspace sent no access token.")
        return self._gmail_send(token, message)

    def end_user_grant(self, *, grant_id: uuid.UUID) -> None:
        """Google's revoke ends the whole grant of this client for the account
        — the caller has made sure no other live identity holds it (F3).
        A token Google no longer knows (400 `invalid_token`) is already ended,
        and so is one the secrets manager no longer holds (a redelivery after
        the token was dropped): there is nothing left that could use it."""

        try:
            refresh_token = self._refresh_token(grant_id)
        except ProviderConfigurationError:
            return
        self._request(
            "oauth2_revoke",
            "POST",
            _GOOGLE_REVOKE,
            data={"token": refresh_token},
            expect=lambda r: r.status_code == 400 and self._oauth_error(r) == "invalid_token",
        )


_ADAPTERS: dict[str, type[_HttpMailAdapter]] = {
    MicrosoftMailAdapter.provider_code: MicrosoftMailAdapter,
    GoogleMailAdapter.provider_code: GoogleMailAdapter,
}


def _register() -> None:
    # Imported lazily, as the other adapters do; `services/mail_*.py` casts the
    # yielded adapter back to MailGateway.
    from app.integration.adapters.base import ProviderAdapter
    from app.integration.services.gateway import register_adapter_factory

    for code, adapter_class in _ADAPTERS.items():
        factory = cast(
            Callable[[Session, IntegrationConnection, uuid.UUID | None, str], ProviderAdapter],
            lambda db, connection, actor_id, purpose, adapter_class=adapter_class: adapter_class(
                db, connection, actor_id, purpose
            ),
        )
        register_adapter_factory(code, factory)


_register()
