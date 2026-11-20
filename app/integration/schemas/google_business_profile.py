"""The Google Business Profile channel over the API (KAN-226 PR-2). Never a
token or a secret reference; Google's resource names and titles as Google
answered them."""

import datetime as dt
import uuid

from pydantic import Field

from app.core.schemas import CamelModel
from app.integration.models.google_business_profile import GbpBindingStatus

_ACCOUNT = r"^accounts/[0-9A-Za-z_-]{1,64}$"
_LOCATION = r"^locations/[0-9A-Za-z_-]{1,64}$"


class BusinessProfileBindingRead(CamelModel):
    id: uuid.UUID
    location_id: uuid.UUID
    location_label: str
    status: GbpBindingStatus
    google_account_name: str | None
    google_location_name: str | None
    google_location_title: str | None
    error_code: str | None
    connected_at: dt.datetime
    version: int


class BusinessProfileBindingPage(CamelModel):
    items: list[BusinessProfileBindingRead]
    # Nexotec's OAuth client at Google is set up (platform staff).
    configured: bool


class BusinessProfileLinkCreate(CamelModel):
    location_id: uuid.UUID


class BusinessProfileLinkStartRead(CamelModel):
    authorize_url: str


class BusinessProfileLinkCallback(CamelModel):
    """The query Google sent the browser back with, passed on by the SPA."""

    state: str = Field(min_length=16, max_length=128)
    code: str | None = Field(default=None, max_length=4096)
    error: str | None = Field(default=None, max_length=256)


class BusinessProfileGoogleChoiceRead(CamelModel):
    account_name: str
    account_display_name: str
    location_name: str
    title: str


class BusinessProfileLinkOutcomeRead(CamelModel):
    binding: BusinessProfileBindingRead
    choices: list[BusinessProfileGoogleChoiceRead]


class BusinessProfileGoogleChoicePage(CamelModel):
    items: list[BusinessProfileGoogleChoiceRead]


class BusinessProfileGoogleLocationPick(CamelModel):
    account_name: str = Field(pattern=_ACCOUNT)
    location_name: str = Field(pattern=_LOCATION)


class BusinessProfileClientStatusRead(CamelModel):
    """Staff's view of Nexotec's own client at Google (KAN-776): the cause
    behind the Betriebe's «interrupted» item. `refusal` is Google's
    identifier only (`SERVICE_DISABLED`, `invalid_client`), never its text."""

    configured: bool
    interrupted: bool
    breaker_open: bool
    refusal: str | None
    refused_at: dt.datetime | None
    last_call_at: dt.datetime | None
    bound_dealerships: int
