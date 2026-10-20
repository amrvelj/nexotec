"""My account (KAN-201): the caller's own profile, signature and connected
accounts under `/v1/me`. Sessions and MFA stay under `/v1/auth/sessions`
and `/v1/auth/mfa/*` (KAN-196) — Anto's ruling of 2026-10-20: one MFA
surface, no moved route."""

import datetime as dt
import uuid

from pydantic import ConfigDict, Field, model_validator

from app.core.i18n import SwissLanguage
from app.core.schemas import CamelModel
from app.core.validators import E164Phone
from app.platform.models.connected_account import ConnectedAccountProvider, ConnectedAccountStatus
from app.platform.schemas.patch import refuse_null_on_not_null_fields


class MeRead(CamelModel):
    """The caller's own record as My account shows it. The e-mail address is
    read-only here: it is the sign-in and recovery channel, changed by a
    manager while the user is invited and by platform staff afterwards
    (KAN-196) — never by the user themselves (an open question with Anto since KAN-196).
    The UI language is a preference (`/v1/me/preferences/ui`), not a field."""

    id: uuid.UUID
    dealership_id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    phone: str | None
    direct_dial: str | None
    correspondence_language: SwissLanguage | None
    # What `correspondenceLanguage: null` means: the Betrieb's default.
    default_correspondence_language: SwissLanguage
    signature_block: str | None
    has_signature_image: bool
    version: int


class MeUpdate(CamelModel):
    """PATCH semantics: a field left out is left unchanged. Names are NOT
    NULL (an explicit null is a 422); the others may be cleared with null.
    Everything else on the user — roles, status, sites, the validity window,
    the e-mail address — is the manager's (the Users screen), so a field this
    model does not name is a 422, never silently dropped."""

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, max_length=100, min_length=1)
    last_name: str | None = Field(default=None, max_length=100, min_length=1)
    phone: E164Phone | None = None
    direct_dial: E164Phone | None = None
    correspondence_language: SwissLanguage | None = None
    signature_block: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _names_are_not_cleared(self) -> "MeUpdate":
        refuse_null_on_not_null_fields(
            self, nullable=frozenset({"phone", "direct_dial", "correspondence_language", "signature_block"})
        )
        return self


class ConnectedAccountRead(CamelModel):
    """What the user and the manager see of a grant: provider, account,
    scopes, status and stamps — never the secret reference, never content."""

    id: uuid.UUID
    provider: ConnectedAccountProvider
    external_account_label: str | None
    scopes: list[str]
    status: ConnectedAccountStatus
    linked_at: dt.datetime
    last_used_at: dt.datetime | None
    revoked_at: dt.datetime | None
    revoked_reason: str | None
    version: int


class ConnectedAccountListRead(CamelModel):
    items: list[ConnectedAccountRead]
