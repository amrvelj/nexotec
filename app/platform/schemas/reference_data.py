import datetime as dt
import uuid

from pydantic import Field

from app.core.schemas import CamelModel


class ReferenceValueCreate(CamelModel):
    """list_code is not part of the body — it comes from the
    POST /v1/reference-data/{list_code} path, same shape as User under Dealership.
    """

    value_code: str = Field(max_length=64, min_length=1)
    label_de: str = Field(max_length=200, min_length=1)
    label_fr: str = Field(max_length=200, min_length=1)
    label_it: str = Field(max_length=200, min_length=1)
    label_en: str = Field(max_length=200, min_length=1)
    sort_order: int = 0
    active: bool = True


class ReferenceValueUpdate(CamelModel):
    """value_code is immutable — other entities reference it by that string,
    so it isn't offered as an update field (PATCH is labels/ordering/active
    only).
    """

    label_de: str | None = Field(default=None, max_length=200, min_length=1)
    label_fr: str | None = Field(default=None, max_length=200, min_length=1)
    label_it: str | None = Field(default=None, max_length=200, min_length=1)
    label_en: str | None = Field(default=None, max_length=200, min_length=1)
    sort_order: int | None = None
    active: bool | None = None


class ReferenceValueRead(CamelModel):
    id: uuid.UUID
    list_code: str
    value_code: str
    label_de: str
    label_fr: str
    label_it: str
    label_en: str
    sort_order: int
    active: bool
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


class ReferenceValuePage(CamelModel):
    items: list[ReferenceValueRead]
    next_cursor: str | None


class ReferenceListRead(CamelModel):
    """One canonical list, for the `/settings/reference` list picker.

    `valueCount` / `activeValueCount` are cheap aggregates the screen shows
    beside each list name; `listCode` stays the stable key other entities
    and the URL (`?list=`) reference.
    """

    list_code: str
    label_de: str
    label_fr: str
    label_it: str
    label_en: str
    value_count: int
    active_value_count: int
    created_at: dt.datetime
    updated_at: dt.datetime


class ReferenceListCollection(CamelModel):
    """A plain wrapped collection, not a page: the set of canonical lists is
    fixed and seed-only (a new list is an alembic migration, never a POST),
    ~24 rows, so keyset pagination would be ceremony with no payoff. The
    wrapper object leaves room to add fields later without a breaking change.
    """

    items: list[ReferenceListRead]
