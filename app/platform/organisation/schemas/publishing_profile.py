"""Publishing profile (KAN-225; PRD Publishing profile, FR-A-04/05/27).

Localised texts travel as `{"de": "…", "fr": …}`: only the four languages,
an empty or blank text drops its language (so an override row inherits it
again), and a PATCH replaces the whole map of a field it names. Every money
figure is gross CHF incl. MwSt (ADR-057).
"""

import datetime as dt
import re
import uuid
from decimal import Decimal
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app.core.auth import AccessRole
from app.core.schemas import CamelModel
from app.platform.organisation.models.branding_asset import PublishingImageKind
from app.platform.organisation.models.publishing_profile import PROFILE_LANGUAGES, SOCIAL_NETWORKS

LocalisedText = dict[str, str]

_MAX_TEXT = {
    "short_description": 300,
    "long_description": 5000,
    "impressum": 3000,
    "privacy_notice": 20000,
    "withdrawal_wording": 5000,
    "warranty_text": 2000,
    "mfk_statement": 1000,
    "price_display_wording": 120,
    "standard_equipment_blurb": 2000,
    "trade_in_promotion_text": 1000,
}
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")


def clean_localised(value: LocalisedText | None, *, field: str) -> LocalisedText | None:
    if value is None:
        return None
    out: LocalisedText = {}
    limit = _MAX_TEXT[field]
    for language, text in value.items():
        if language not in PROFILE_LANGUAGES:
            raise ValueError(f"'{language}' is not a language of the profile (de, fr, it, en).")
        if not isinstance(text, str):
            raise ValueError("A localised text is a string.")  # noqa: TRY004 — pydantic answers a ValueError with 422
        stripped = text.strip()
        if not stripped:
            continue
        if len(stripped) > limit:
            raise ValueError(f"The text is longer than {limit} characters.")
        out[language] = stripped
    return out


def _check_url(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if not value.startswith("https://") or len(value) > 500 or " " in value:
        raise ValueError("Give the full address, starting with https://.")
    return value


class _OverridableFields(CamelModel):
    # A field the schema does not know is refused, not ignored: a legal
    # notice sent for a location must not vanish silently (they are the
    # Betrieb's alone).
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True, extra="forbid")

    short_description: LocalisedText | None = None
    long_description: LocalisedText | None = None
    warranty_text: LocalisedText | None = None
    mfk_statement: LocalisedText | None = None
    price_display_wording: LocalisedText | None = None
    standard_equipment_blurb: LocalisedText | None = None
    trade_in_promotion_text: LocalisedText | None = None
    delivery_fee_chf: Decimal | None = Field(default=None, ge=0, le=100000, decimal_places=2)
    financing_term_months: int | None = Field(default=None, ge=1, le=120)
    financing_down_payment_chf: Decimal | None = Field(default=None, ge=0, le=10000000, decimal_places=2)
    financing_residual_value_chf: Decimal | None = Field(default=None, ge=0, le=10000000, decimal_places=2)
    financing_nominal_rate_pct: Decimal | None = Field(default=None, ge=0, le=30, decimal_places=3)
    financing_effective_rate_pct: Decimal | None = Field(default=None, ge=0, le=30, decimal_places=3)
    financing_provider: str | None = Field(default=None, max_length=200)

    @field_validator(
        "short_description",
        "long_description",
        "warranty_text",
        "mfk_statement",
        "price_display_wording",
        "standard_equipment_blurb",
        "trade_in_promotion_text",
    )
    @classmethod
    def _localised(cls, value: LocalisedText | None, info) -> LocalisedText | None:
        return clean_localised(value, field=info.field_name)


class PublishingProfileUpdate(_OverridableFields):
    """The Betrieb's profile, partial. A localised field named with `{}` (or
    null) clears it."""

    website_domain: str | None = None
    is_published: bool | None = None
    social_profiles: dict[str, str] | None = None
    impressum: LocalisedText | None = None
    privacy_notice: LocalisedText | None = None
    withdrawal_wording: LocalisedText | None = None
    agb_url: str | None = None

    @field_validator("impressum", "privacy_notice", "withdrawal_wording")
    @classmethod
    def _legal_localised(cls, value: LocalisedText | None, info) -> LocalisedText | None:
        return clean_localised(value, field=info.field_name)

    @field_validator("website_domain")
    @classmethod
    def _domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
        if not value:
            return None
        if not _DOMAIN_RE.match(value):
            raise ValueError("Give a domain such as garage-muster.ch, without https:// or a path.")
        return value

    @field_validator("agb_url")
    @classmethod
    def _agb_url(cls, value: str | None) -> str | None:
        return _check_url(value)

    @field_validator("social_profiles")
    @classmethod
    def _social(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        out: dict[str, str] = {}
        for network, url in value.items():
            if network not in SOCIAL_NETWORKS:
                raise ValueError(f"'{network}' is not a supported social network ({', '.join(SOCIAL_NETWORKS)}).")
            checked = _check_url(url)
            if checked:
                out[network] = checked
        return out

    @model_validator(mode="after")
    def _published_is_not_null(self) -> "PublishingProfileUpdate":
        if "is_published" in self.model_fields_set and self.is_published is None:
            raise ValueError("isPublished cannot be null.")
        return self


class LocationOverrideUpdate(_OverridableFields):
    """A location's override, partial. A null (or an empty map) inherits the
    Betrieb's value again."""


class _ProfileFieldsRead(CamelModel):
    short_description: LocalisedText
    long_description: LocalisedText
    warranty_text: LocalisedText
    mfk_statement: LocalisedText
    price_display_wording: LocalisedText
    standard_equipment_blurb: LocalisedText
    trade_in_promotion_text: LocalisedText
    delivery_fee_chf: Decimal | None
    financing_term_months: int | None
    financing_down_payment_chf: Decimal | None
    financing_residual_value_chf: Decimal | None
    financing_nominal_rate_pct: Decimal | None
    financing_effective_rate_pct: Decimal | None
    financing_provider: str | None


class AgbDocumentRead(CamelModel):
    byte_size: int
    uploaded_at: dt.datetime


class LocationOverrideRead(_ProfileFieldsRead):
    location_id: uuid.UUID


class PublishingProfileRead(_ProfileFieldsRead):
    dealership_id: uuid.UUID
    # Read from the screens that own them — shown, never written here.
    trade_name: str | None
    website: str | None
    website_domain: str | None
    is_published: bool
    social_profiles: dict[str, str]
    impressum: LocalisedText
    privacy_notice: LocalisedText
    withdrawal_wording: LocalisedText
    agb_url: str | None
    agb_document: AgbDocumentRead | None
    location_overrides: list[LocationOverrideRead]
    lead_routing: list["LeadRoutingRead"]
    images: list["PublishingImageRead"]
    # The one version of the whole publishing screen: every write on it —
    # profile, override, AGB, imagery, lead routing — takes it as If-Match.
    version: int
    updated_at: dt.datetime


class EffectiveProfileRead(_ProfileFieldsRead):
    """What a channel publishes for a site: the override over the Betrieb."""

    dealership_id: uuid.UUID
    location_id: uuid.UUID | None
    trade_name: str | None
    website_domain: str | None
    is_published: bool
    social_profiles: dict[str, str]
    impressum: LocalisedText
    privacy_notice: LocalisedText
    withdrawal_wording: LocalisedText
    agb_url: str | None
    has_agb_document: bool


Channel = Literal["website"]


class MissingItemRead(CamelModel):
    """One thing the channel needs that the profile lacks. `code` is stable
    — the SPA renders it in the dealer's words (`publishingValidation.<code>`)
    — and `section` names where to fix it."""

    code: str
    section: Literal["identity", "legal", "leadRouting", "imagery", "listingDefaults"]
    location_id: uuid.UUID | None = None
    location_name: str | None = None


class ProfileValidationRead(CamelModel):
    channel: Channel
    ready: bool
    missing: list[MissingItemRead]


class LeadRoutingUpdate(CamelModel):
    lead_email: str | None = Field(default=None, max_length=254)
    lead_phone: str | None = Field(default=None, max_length=20)
    creates_task: bool = False
    recipient_user_id: uuid.UUID | None = None
    recipient_role: AccessRole | None = None

    @field_validator("lead_email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if "@" not in value or " " in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("Give a valid e-mail address.")
        return value

    @field_validator("lead_phone")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _one_recipient(self) -> "LeadRoutingUpdate":
        if self.recipient_user_id is not None and self.recipient_role is not None:
            raise ValueError("Name a user or a role as the recipient, not both.")
        if self.recipient_role == AccessRole.PLATFORM_ADMIN:
            raise ValueError("Platform staff never receive a Betrieb's enquiries.")
        return self


class LeadRoutingRead(CamelModel):
    location_id: uuid.UUID
    location_name: str
    lead_email: str | None
    lead_phone: str | None
    creates_task: bool
    recipient_user_id: uuid.UUID | None
    recipient_role: AccessRole | None


class PublishingImageRead(CamelModel):
    id: uuid.UUID
    location_id: uuid.UUID
    kind: PublishingImageKind
    position: int
    is_primary: bool
    mime_type: str
    width: int | None
    height: int | None
    byte_size: int
    uploaded_at: dt.datetime


class ImageOrderUpdate(CamelModel):
    image_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


PublishingProfileRead.model_rebuild()
