"""The Google Business Profile channel over the API (KAN-226 PR-2). Never a
token or a secret reference; Google's resource names and titles as Google
answered them."""

import datetime as dt
import uuid
from typing import Literal

from pydantic import Field

from app.core.schemas import CamelModel
from app.integration.models.google_business_profile import GbpBindingStatus, GbpDriftOutcome

_ACCOUNT = r"^accounts/[0-9A-Za-z_-]{1,64}$"
_LOCATION = r"^locations/[0-9A-Za-z_-]{1,64}$"


class BusinessProfileDriftRead(CamelModel):
    """KAN-226 PR-4: the binding's latest nightly drift check. `driftedFields`
    are field codes (`address`, `phone`, `website`, `regularHours`,
    `moreHours`, `specialHours`), never a value read from Google; `reason` a
    closed code for a failed or skipped check."""

    checked_at: dt.datetime
    outcome: GbpDriftOutcome
    drifted_fields: list[str]
    reason: str | None


class BusinessProfilePhotosRead(CamelModel):
    """KAN-821: what the photo upload did with the location's imagery, from
    Nexotec's own records (`gbp_photo_summary`), never read from Google."""

    uploaded: int
    refused: int
    waiting: int
    stopped: bool


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
    # KAN-226 PR-4, on the bindings list: the latest drift check (None before
    # the first) and the departments the last push could not show at Google.
    drift: BusinessProfileDriftRead | None = None
    unmapped_departments: list[str] = Field(default_factory=list)
    # KAN-821, on the bindings list: the «Fotos» line; None for a binding that is not active.
    photos: BusinessProfilePhotosRead | None = None


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


class BusinessProfileClientCheckRead(CamelModel):
    """«Erneut prüfen» (KAN-776): what Google answered this time —
    `answered` (client and project accepted), `refused` (refused again) or
    `unavailable` — and the client's state after it."""

    outcome: Literal["answered", "refused", "unavailable"]
    status: BusinessProfileClientStatusRead
