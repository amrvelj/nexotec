"""BFE energy rating lookup (WP-5 PR-8, ADR-042)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.vehicle.models.energy_rating import ModelVariantEnergyLabel, ModelVariantEnergyRating


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
        # KAN-547: a row the identAPI's label filled first (`source = auto_i_dat`)
        # becomes the official one once the BFE import writes it.
        existing.source = "bfe"
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


def energy_rating_for_configuration(
    db: Session, *, model_variant_id: uuid.UUID | None, first_registration_year: int | None, model_year: int | None
) -> ModelVariantEnergyRating | None:
    """KAN-479 (Anto, option A) / KAN-547: the Energieetikette a configuration
    shows — the catalogue variant's rating for the car's year: the first
    registration's year, else the model year, else the newest year held (a
    new car not yet registered). Nothing without a catalogue variant, and
    never a guess across years when the car's year is known and unrated."""

    if model_variant_id is None:
        return None
    year = first_registration_year or model_year
    if year is not None:
        return get_energy_rating_for_year(db, model_variant_id=model_variant_id, rating_year=year)
    return db.scalar(
        select(ModelVariantEnergyRating)
        .where(ModelVariantEnergyRating.model_variant_id == model_variant_id)
        .order_by(ModelVariantEnergyRating.rating_year.desc())
        .limit(1)
    )


def energy_labels_for_configuration(
    db: Session, *, model_variant_id: uuid.UUID | None, first_registration_year: int | None, model_year: int | None
) -> list[ModelVariantEnergyLabel]:
    """KAN-597 (ADR-142): the variant's per-gearbox Energieetiketten for the
    year `energy_rating_for_configuration` reads — the first registration's
    year, else the model year, else the newest year held — ordered by
    gearbox. Read on its own year rule, not through the rating: a year whose
    gearboxes disagree has labels but no rating, and that is when the seller
    needs to see both."""

    if model_variant_id is None:
        return []
    year = first_registration_year or model_year
    if year is None:
        year = db.scalar(
            select(ModelVariantEnergyLabel.label_year)
            .where(ModelVariantEnergyLabel.model_variant_id == model_variant_id)
            .order_by(ModelVariantEnergyLabel.label_year.desc())
            .limit(1)
        )
        if year is None:
            return []
    return list(
        db.scalars(
            select(ModelVariantEnergyLabel)
            .where(ModelVariantEnergyLabel.model_variant_id == model_variant_id, ModelVariantEnergyLabel.label_year == year)
            .order_by(ModelVariantEnergyLabel.gearbox)
        )
    )
