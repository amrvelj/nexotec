"""BFE energy/emission ratings (WP-5 PR-8, ADR-042). Sourced from the
Bundesamt für Energie, not auto-i-dat — a rare case of catalogue data that
genuinely is ours to import, since it's official Swiss public data.

The load-bearing design point: energy_efficiency_category is stored WITH
the year it applied to, never as a flat property of the variant. The A–G
Energieetikette scale is relative to the current new-car fleet and is
recalculated annually by UVEK, so the SAME model_variant can legitimately
carry a different category in 2024 than in 2026 without anything about the
car changing. One row per (model_variant, rating_year) — looking up "this
variant's category" always means "...for year Y", never a bare property.
A variant absent from a given year's BFE import simply has no row for that
year — absent is honest, a guessed/carried-forward value is not.
"""

import uuid

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import PrimaryKeyMixin, TimestampMixin
from app.core.types import GUID
from app.db import Base
from app.vehicle.models.catalogue import ModelVariant


class ModelVariantEnergyRating(PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vehicle_model_variant_energy_rating"
    __table_args__ = (
        UniqueConstraint(
            "model_variant_id", "rating_year", name="uq_vehicle_model_variant_energy_rating_variant_year"
        ),
    )

    model_variant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicle_model_variant.id"), nullable=False, index=True
    )
    rating_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Reference-value codes (PR-1's energy_efficiency_category/
    # emission_standard/consumption_norm lists) — nullable individually,
    # since BFE may publish one without the others for a given year.
    energy_efficiency_category: Mapped[str | None] = mapped_column(String(4), nullable=True)
    emission_standard: Mapped[str | None] = mapped_column(String(16), nullable=True)
    consumption_norm: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="bfe")

    model_variant: Mapped[ModelVariant] = relationship()
