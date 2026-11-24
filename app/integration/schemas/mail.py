"""Contact mail's connection screens over the API (KAN-215). Never a token,
a secret reference or a provider tenant id; a colleague's own mailbox is
never listed to anyone but its owner (D-A-15)."""

import datetime as dt
import uuid

from pydantic import EmailStr, Field

from app.core.schemas import CamelModel
from app.integration.models.mail import MailProviderCode, SendingIdentityKind, SendingIdentityStatus


class MailProviderRead(CamelModel):
    code: MailProviderCode
    # Nexotec's app registration at the provider is set up (platform staff).
    configured: bool


class MailboxRead(CamelModel):
    id: uuid.UUID
    kind: SendingIdentityKind
    provider: MailProviderCode
    mailbox_address: str
    status: SendingIdentityStatus
    error_code: str | None
    connected_at: dt.datetime
    version: int


class BusinessMailboxRead(CamelModel):
    mailbox: MailboxRead | None
    providers: list[MailProviderRead]


class SenderRead(CamelModel):
    """What `resolve_sender` answers for the caller right now (D1 A)."""

    kind: SendingIdentityKind
    address: str


class OwnMailboxRead(CamelModel):
    mailbox: MailboxRead | None
    # Exactly one of the two: the address contacts see, or why nothing can be sent.
    sender: SenderRead | None
    sender_refusal: str | None
    providers: list[MailProviderRead]


class BusinessLinkCreate(CamelModel):
    provider: MailProviderCode
    mailbox_address: EmailStr = Field(max_length=320)


class UserLinkCreate(CamelModel):
    provider: MailProviderCode


class LinkStartRead(CamelModel):
    authorize_url: str


class LinkCallback(CamelModel):
    """The query the provider sent the browser back with, passed on by the SPA
    as it came. `tenant` and `adminConsent` are only compared, never trusted."""

    state: str = Field(min_length=16, max_length=128)
    code: str | None = Field(default=None, max_length=4096)
    error: str | None = Field(default=None, max_length=256)
    admin_consent: str | None = Field(default=None, max_length=16)
    tenant: str | None = Field(default=None, max_length=255)


class LinkOutcomeRead(CamelModel):
    kind: SendingIdentityKind
    # `connected`, or `consent_required` with `redirectUrl` (Microsoft's admin consent).
    status: str
    redirect_url: str | None
    mailbox_id: uuid.UUID | None
