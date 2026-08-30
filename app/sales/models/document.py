"""SalesDocument (WP-8 PR-7) — append-only, one row per generation. There
is no "entity + immutable version-history child" precedent anywhere in
this codebase (confirmed by research) and no blob storage either
(app.inventory.models.stock_item_publishing.StockItemMedia.url is "an
external reference only") — so the PDF itself is never stored. The
`content_definition` JSON IS the frozen artifact; the actual bytes are
re-rendered deterministically from it, on demand, via
app.platform.public.render_document, every time a version is downloaded.
Re-rendering is not byte-identical if the dealership's own letterhead/
logo changes between generation and a later reprint — the CONTENT is
frozen, the stationery is current (an accepted, product-flagged property).

`version` is set once, at generation, and NEVER incremented on edit —
there is no edit path for a generated document at all; a changed offer
generates a NEW document row with the next version number instead.
"""

import datetime as dt
import enum
import uuid
from typing import Any

from sqlalchemy import JSON, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin
from app.core.types import GUID, UTCDateTime
from app.db import Base


class DocumentOwnerType(str, enum.Enum):
    OFFER = "offer"
    CONTRACT = "contract"


class SalesDocument(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "sales_document"

    owner_type: Mapped[DocumentOwnerType] = mapped_column(
        SAEnum(DocumentOwnerType, native_enum=False, length=16), nullable=False
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    correspondence_language: Mapped[str] = mapped_column(String(2), nullable=False)
    # The exact app.platform.public.ContentDefinition that was rendered —
    # re-rendered from THIS, deterministically, on every future download.
    content_definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    rendered_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    rendered_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
