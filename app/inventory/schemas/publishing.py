import datetime as dt
import uuid

from pydantic import Field

from app.core.schemas import CamelModel
from app.inventory.models.stock_item_publishing import MarketplaceChannel, PublishingState, TransmissionStatus


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
    transmission_status: TransmissionStatus
    last_transmission_error: str | None
    last_attempted_at: dt.datetime | None
    blocking_conditions: list[BlockingCondition] = Field(default_factory=list)
    version: int


class UnpublishRequest(CamelModel):
    confirm: bool


class EquipmentRead(CamelModel):
    """§ ADR-062 — the vehicle's equipment as the publishing tab reads it
    (owned and edited in app.vehicle). Declared so schema.d.ts carries
    the type rather than the frontend hand-writing one (KAN-35)."""

    ausstattung_codes: list[str]
    extras: list[str]
    eigenschaften: list[str]
    provider_ausstattung: dict[str, str]


class MediaRead(CamelModel):
    id: uuid.UUID
    position: int
    url: str


class AddMediaRequest(CamelModel):
    url: str


class ReorderMediaRequest(CamelModel):
    ordered_media_ids: list[uuid.UUID]
