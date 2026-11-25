"""The planner-resources API shapes (KAN-808, S7)."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, ConfigDict, Field

from app.core.i18n import SwissLanguage
from app.core.schemas import CamelModel
from app.core.validators import DecimalStr
from app.planning.models.lists import NAME_LENGTH
from app.planning.models.resource import ResourceKind


def _a_name(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("a name is not blank.")
    return cleaned


Name = Annotated[str, Field(min_length=1, max_length=NAME_LENGTH), AfterValidator(_a_name)]
# Only the languages translated (ruling 176); an empty text drops the language.
NameTranslations = dict[SwissLanguage, Annotated[str, Field(max_length=NAME_LENGTH)]]
SortOrder = Annotated[int, Field(ge=0, le=9999)]
Rank = Annotated[int, Field(ge=1, le=99)]
# Counted time = clocked time × factor (above 0, at most 9.99, two decimals).
CountedTimeFactor = Annotated[DecimalStr, Field(gt=0, le=Decimal("9.99"), max_digits=3, decimal_places=2)]


# --- the lists --------------------------------------------------------------------------------


class _EntryRead(CamelModel):
    id: uuid.UUID
    name: str
    name_translations: dict[str, str]
    sort_order: int
    version: int
    created_at: dt.datetime


class WorkplaceTypeRead(_EntryRead):
    # A copy of Nexotec's starting set the manager has not changed or confirmed yet (ADR-222).
    is_sample: bool
    seed_code: str | None


class TechnicianTypeRead(WorkplaceTypeRead):
    rank: int | None
    # None: counted time is clocked time.
    counted_time_factor: Decimal | None


class QualificationRead(_EntryRead):
    # Stored only: nothing enforces it yet (Anto, 2026-11-25).
    is_mandatory: bool


class WorkplaceTypeList(CamelModel):
    items: list[WorkplaceTypeRead]


class TechnicianTypeList(CamelModel):
    items: list[TechnicianTypeRead]


class QualificationList(CamelModel):
    items: list[QualificationRead]


class _EntryCreate(CamelModel):
    # An unknown field is a schema 422, never silently dropped (KAN-808 review).
    model_config = ConfigDict(extra="forbid")

    name: Name
    name_translations: NameTranslations | None = None
    sort_order: SortOrder = 0


class _EntryUpdate(CamelModel):
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    # null clears every translation.
    name_translations: NameTranslations | None = None
    sort_order: SortOrder | None = None


class WorkplaceTypeCreate(_EntryCreate):
    pass


class WorkplaceTypeUpdate(_EntryUpdate):
    pass


class TechnicianTypeCreate(_EntryCreate):
    rank: Rank | None = None
    counted_time_factor: CountedTimeFactor | None = None


class TechnicianTypeUpdate(_EntryUpdate):
    # null clears the rank or the factor.
    rank: Rank | None = None
    counted_time_factor: CountedTimeFactor | None = None


class QualificationCreate(_EntryCreate):
    is_mandatory: bool = False


class QualificationUpdate(_EntryUpdate):
    is_mandatory: bool | None = None


# --- resources --------------------------------------------------------------------------------


class WorkingDay(CamelModel):
    """One working weekday (ISO: 1 = Monday … 7 = Sunday), local times to the
    minute, e.g. 07:30–12:00 and 13:00–17:00."""

    model_config = ConfigDict(extra="forbid")

    weekday: int
    starts_at: dt.time
    ends_at: dt.time
    second_starts_at: dt.time | None = None
    second_ends_at: dt.time | None = None


class WorkingHoursUpdate(CamelModel):
    """The person's whole week, replacing what is stored (PUT, If-Match on the
    resource's version). A weekday that is not sent is not worked."""

    model_config = ConfigDict(extra="forbid")

    days: list[WorkingDay] = Field(max_length=7)


class ResourceRead(CamelModel):
    id: uuid.UUID
    kind: ResourceKind
    location_id: uuid.UUID
    location_label: str
    # A person's user and name; null for a workplace.
    user_id: uuid.UUID | None
    user_label: str | None
    # False: the user no longer works in the business (left, suspended or
    # no longer a member) — shown as deactivated. Null for a workplace.
    person_acts_here: bool | None
    # A workplace's name; null for a person.
    name: str | None
    workplace_type_id: uuid.UUID | None
    technician_type_id: uuid.UUID | None
    qualification_ids: list[uuid.UUID]
    working_hours: list[WorkingDay]
    # Whether the resource appears in the planner.
    is_active: bool
    sort_order: int
    version: int
    created_at: dt.datetime


class ResourceList(CamelModel):
    items: list[ResourceRead]


class PersonCreate(CamelModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[ResourceKind.PERSON]
    user_id: uuid.UUID
    location_id: uuid.UUID
    technician_type_id: uuid.UUID | None = None
    qualification_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    sort_order: SortOrder = 0


class WorkplaceCreate(CamelModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[ResourceKind.WORKPLACE]
    name: Name
    location_id: uuid.UUID
    workplace_type_id: uuid.UUID | None = None
    sort_order: SortOrder = 0


ResourceCreate = Annotated[PersonCreate | WorkplaceCreate, Field(discriminator="kind")]


class ResourceUpdate(CamelModel):
    """Each field sent is changed; a field of the other kind is refused.
    `workplaceTypeId` and `technicianTypeId` clear with null. A field no
    resource has (`userId`, `kind`, …) is a schema 422, never silently dropped."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    location_id: uuid.UUID | None = None
    workplace_type_id: uuid.UUID | None = None
    technician_type_id: uuid.UUID | None = None
    qualification_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    sort_order: SortOrder | None = None


class PersonCandidateRead(CamelModel):
    user_id: uuid.UUID
    display_name: str


class PersonCandidateList(CamelModel):
    """Who may become a person resource: the users who work in the business
    (homed or an active member, active or invited) and are none yet."""

    items: list[PersonCandidateRead]
