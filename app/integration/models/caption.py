"""IntegrationProviderCaption (KAN-547, ADR-111 (3)) — a provider's own
vocabulary: what each code it sends means, in the four languages.

The identAPI delivers every coded field (`fuel`, `body`, `drive`, `gearbox`,
`type`, `category`, the option groups, …) as a bare code and resolves them
through one `GET /caption/code?lang=` per language. Captions are *vocabulary,
not vehicle data* (ADR-111 (3)): they are global — one row per
(provider, caption group, list, code), no `tenant_id`, whichever Betrieb's
connection fetched them — and they live in the integration registry beside
the provider row, because the one action that fetches them, «Test connection»,
runs here (`services/gateway.py`). `app.vehicle` reads them through
`integration.public` to show a provider's wording beside an unmapped code in
the mapping-gap queue; it never copies them into its own reference lists
(ADR-044: provider text is rendered as delivered).

`kind` is the list a caption sits in as the wire names it — `passenger`,
`utility`, `moto` or `global` (a code every vehicle type shares). The four
label columns are filled one language at a time; a language not fetched yet
is NULL. No daily refresh: a background job has no user to enrol
(ADR-111 as amended), so captions are as old as the last «Test connection»
(`fetched_at`).
"""

import datetime as dt

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin
from app.core.types import UTCDateTime
from app.db import Base


class IntegrationProviderCaption(PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_provider_caption"
    __table_args__ = (
        UniqueConstraint(
            "provider", "caption_group", "kind", "code", name="uq_integration_provider_caption_natural_key"
        ),
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    caption_group: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    label_de: Mapped[str | None] = mapped_column(String(200), nullable=True)
    label_fr: Mapped[str | None] = mapped_column(String(200), nullable=True)
    label_it: Mapped[str | None] = mapped_column(String(200), nullable=True)
    label_en: Mapped[str | None] = mapped_column(String(200), nullable=True)
    label_short_de: Mapped[str | None] = mapped_column(String(80), nullable=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
