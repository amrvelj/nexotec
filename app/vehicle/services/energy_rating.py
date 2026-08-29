"""BFE energy rating lookup (WP-5 PR-8, ADR-042)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.vehicle.models.energy_rating import ModelVariantEnergyRating


def get_energy_rating_for_year(
    db: Session, *, model_variant_id: uuid.UUID, rating_year: int
) -> ModelVariantEnergyRating | None:
    """Returns the rating recorded for EXACTLY this year, or None. Never
    falls back to the nearest available year — a car BFE hasn't rated for
    2026 has no 2026 category, full stop, per the PRD's own "absent is
    honest, guessed is not" rule.
    """

    return db.scalar(
        select(ModelVariantEnergyRating).where(
            ModelVariantEnergyRating.model_variant_id == model_variant_id,
            ModelVariantEnergyRating.rating_year == rating_year,
        )
    )


def upsert_energy_rating(
    db: Session,
    *,
    model_variant_id: uuid.UUID,
    rating_year: int,
    energy_efficiency_category: str | None,
    emission_standard: str | None,
    consumption_norm: str | None,
) -> ModelVariantEnergyRating:
    existing = db.scalar(
        select(ModelVariantEnergyRating).where(
            ModelVariantEnergyRating.model_variant_id == model_variant_id,
            ModelVariantEnergyRating.rating_year == rating_year,
        )
    )
    if existing is not None:
        existing.energy_efficiency_category = energy_efficiency_category
        existing.emission_standard = emission_standard
        existing.consumption_norm = consumption_norm
        db.flush()
        return existing

    rating = ModelVariantEnergyRating(
        model_variant_id=model_variant_id,
        rating_year=rating_year,
        energy_efficiency_category=energy_efficiency_category,
        emission_standard=emission_standard,
        consumption_norm=consumption_norm,
    )
    db.add(rating)
    db.flush()
    return rating
