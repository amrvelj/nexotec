import datetime as dt
import uuid

from pydantic import Field

from app.core.schemas import CamelModel
from app.inventory.models.stock_item_publishing import MarketplaceChannel, PublishingState


class BlockingCondition(CamelModel):
    """`field` is the marketplace's OWN field name (never inventory's own
    internal name) — computed and named before send, per ADR-062."""

    field: str
    message: str


class ListingTextUpdate(CamelModel):
    zusatztitel: str | None = None
    bemerkungen: str | None = None
    zustandsbeschreibung: str | None = None
    haendlerbemerkungen: str | None = None
    youtube_url: str | None = None
    pdf_document_ref: str | None = None


class PublishingRead(CamelModel):
    id: uuid.UUID
    stock_item_id: uuid.UUID
    channel: MarketplaceChannel
    state: PublishingState
    zusatztitel: str | None
    bemerkungen: str | None
    zustandsbeschreibung: str | None
    haendlerbemerkungen: str | None
    youtube_url: str | None
    pdf_document_ref: str | None
    last_published_at: dt.datetime | None
    blocking_conditions: list[BlockingCondition] = Field(default_factory=list)
    version: int


class UnpublishRequest(CamelModel):
    confirm: bool


class MediaRead(CamelModel):
    id: uuid.UUID
    position: int
    url: str


class AddMediaRequest(CamelModel):
    url: str


class ReorderMediaRequest(CamelModel):
    ordered_media_ids: list[uuid.UUID]
