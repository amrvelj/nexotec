import datetime as dt
import uuid

from pydantic import Field

from app.core.schemas import CamelModel


class BrandCreate(CamelModel):
    code: str = Field(max_length=64, min_length=1)
    display_name: str = Field(max_length=120, min_length=1)


class BrandUpdate(CamelModel):
    display_name: str | None = Field(default=None, max_length=120, min_length=1)


class BrandRead(CamelModel):
    id: uuid.UUID
    code: str
    display_name: str
    version: int = 1
    created_at: dt.datetime
    updated_at: dt.datetime


class BrandPage(CamelModel):
    items: list[BrandRead]
    next_cursor: str | None


class MappingGapRead(CamelModel):
    id: uuid.UUID
    provider: str
    vehicle_kind: str
    code_group: str
    provider_code: str
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime
    occurrences: int
    resolved: bool
    resolved_at: dt.datetime | None
    resolved_value_code: str | None


class MappingGapPage(CamelModel):
    items: list[MappingGapRead]
    next_cursor: str | None


class MappingGapResolve(CamelModel):
    canonical_list_code: str = Field(max_length=64, min_length=1)
    canonical_value_code: str = Field(max_length=64, min_length=1)
