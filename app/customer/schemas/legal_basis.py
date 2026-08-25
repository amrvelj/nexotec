import datetime as dt
import uuid

from pydantic import Field

from app.core.schemas import CamelModel


class LegalBasisCreate(CamelModel):
    basis: str = Field(max_length=64, min_length=1)
    scope: str = Field(min_length=1)
    source_document: str = Field(min_length=1)


class LegalBasisRead(CamelModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    group_id: uuid.UUID
    basis: str
    scope: str
    granted_at: dt.datetime
    withdrawn_at: dt.datetime | None
    source_document: str
