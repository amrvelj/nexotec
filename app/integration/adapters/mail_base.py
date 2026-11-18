"""The mailbox port (KAN-215): what the two mailbox providers — Microsoft 365
(Graph `sendMail`) and Google Workspace (Gmail `messages.send`) — do for
Nexotec, behind one shape. Customer mail leaves only through the dealer's own
mailbox (Anto, 2026-11-14); nothing outside `adapters/` knows a provider's
endpoint.

Every request an adapter sends is metered (`services/metering.py`) inside the
caller's `call_capability` block, so each one is a call-log row and the block
is the unit of the circuit breaker, as for every other provider.

Two ways to send:
- `send_as_user` — the author's own mailbox, a delegated grant: the refresh
  token lives in the secrets manager under the grant's id (platform's
  connected-account row, FR-AU-23), read by the adapter alone.
- `send_as_business` — the Betrieb's shared mailbox, by admin consent
  (Anto's Q2 B, 2026-11-15): Microsoft's application permission `Mail.Send`,
  Google's domain-wide delegation. Nexotec's app can send as any mailbox of
  the dealer's tenant unless the dealer's IT restricts it (Exchange
  «RBAC for Applications» / the application access policy; Google: the
  delegation scoped to `gmail.send` only and the service account limited to an
  organisational unit). The identity names the one mailbox sent as.

The link flow's provider half lives here too: the authorisation URL, the code
redemption with PKCE, the id_token's verification (signature from the
provider's published keys, issuer, audience, expiry, nonce) and, for a shared
mailbox, the proof that the person signing in administers the provider tenant
the mailbox belongs to (F1 of the 2026-11-15 review).
"""

import dataclasses
import uuid
from typing import Protocol

from app.integration.errors import ProviderRequestError

MAIL_CATEGORY = "calendar_mail"
MAIL_SEND_CAPABILITY = "mail_send"
MAIL_LINK_CAPABILITY = "mail_link"


class MailLinkRefused(ProviderRequestError):
    """The provider's answer refuses the link, for a reason the dealer can act
    on (`code` is a closed vocabulary the screens translate): never the
    provider's own text, which may name accounts. A `ProviderRequestError`:
    the provider's coherent answer, never charged to the breaker."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclasses.dataclass(frozen=True)
class MailAttachment:
    filename: str
    content_type: str
    content: bytes = dataclasses.field(repr=False)


@dataclasses.dataclass(frozen=True)
class OutgoingMail:
    sender: str
    to: tuple[str, ...]
    cc: tuple[str, ...]
    subject: str
    body_text: str
    attachments: tuple[MailAttachment, ...] = ()


@dataclasses.dataclass(frozen=True)
class CodeGrant:
    """What the provider's token endpoint answered for an authorisation code.
    Token material is kept out of `repr`, so a log line of the grant never
    prints it."""

    id_token: str = dataclasses.field(repr=False)
    access_token: str | None = dataclasses.field(repr=False)
    refresh_token: str | None = dataclasses.field(repr=False)
    scopes: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class VerifiedSignIn:
    """The id_token's claims Nexotec relies on, after verification."""

    subject: str  # Microsoft `oid`, Google `sub`
    email: str | None
    provider_tenant: str  # Microsoft `tid`, Google Workspace `hd`
    admin_roles: tuple[str, ...]  # Microsoft `wids`; empty for Google


@dataclasses.dataclass(frozen=True)
class MailboxInfo:
    external_account_id: str
    address: str


@dataclasses.dataclass(frozen=True)
class SentMail:
    provider_message_id: str | None


class MailGateway(Protocol):
    provider_code: str
    # Microsoft: a shared mailbox needs the admin-consent screen after the
    # administrator's sign-in. Google: the delegation is granted in the Admin
    # console beforehand, and the link proves it by a token for the mailbox.
    needs_admin_consent: bool

    def authorize_url(
        self, *, admin: bool, state: str, nonce: str, code_challenge: str, redirect_uri: str, login_hint: str | None
    ) -> str: ...

    def consent_url(self, *, provider_tenant: str, state: str, redirect_uri: str) -> str | None:
        """The admin-consent screen for a verified tenant, or None when the
        provider has none (`needs_admin_consent`)."""
        ...

    def redeem_code(self, *, code: str, code_verifier: str, redirect_uri: str, admin: bool) -> CodeGrant: ...

    def verify_sign_in(self, grant: CodeGrant, *, nonce: str) -> VerifiedSignIn: ...

    def administers_tenant(self, grant: CodeGrant, sign_in: VerifiedSignIn) -> bool: ...

    def find_business_mailbox(
        self, *, provider_tenant: str, address: str, admin_grant: CodeGrant | None
    ) -> MailboxInfo | None: ...

    def send_as_business(self, *, provider_tenant: str, message: OutgoingMail) -> SentMail: ...

    def send_as_user(self, *, grant_id: uuid.UUID, message: OutgoingMail) -> SentMail: ...

    def end_user_grant(self, *, grant_id: uuid.UUID) -> None: ...
