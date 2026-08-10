import datetime as dt
import uuid

from pydantic import Field

from app.core.schemas import CamelModel


class ReferenceValueCreate(CamelModel):
    """list_code is not part of the body — it comes from the
    POST /v1/reference-data/{list_code} path, same shape as User under Dealer.
    """

    value_code: str = Field(max_length=64, min_length=1)
    label_de: str = Field(max_length=200, min_length=1)
    label_fr: str = Field(max_length=200, min_length=1)
    label_it: str = Field(max_length=200, min_length=1)
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
    sort_order: int | None = None
    active: bool | None = None


class ReferenceValueRead(CamelModel):
    id: uuid.UUID
    list_code: str
    value_code: str
    label_de: str
    label_fr: str
    label_it: str
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
