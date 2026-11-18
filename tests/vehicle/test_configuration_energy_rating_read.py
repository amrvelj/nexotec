"""KAN-547 / KAN-479 (Anto, option A): a configuration's read carries the
Energieetikette with its year — the catalogue variant's rating for the
car's year (first registration, else model year, else the newest held),
`null` when the variant has none for it or the configuration is manual."""

from __future__ import annotations

import uuid

from app.core.auth import AccessRole, create_access_token
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant
from app.vehicle.models.configuration import ConfigurationMatchMethod, ConfigurationMode, ConfigurationSource
from app.vehicle.models.energy_rating import ModelVariantEnergyRating
from app.vehicle.schemas.configuration import ConfigurationCreate
from app.vehicle.services import configuration as configuration_service
from app.vehicle.services.energy_rating import energy_rating_for_configuration


def _variant(db_session) -> ModelVariant:
    brand = Brand(code="t", display_name="Testmarke")
    db_session.add(brand)
    db_session.flush()
    group = ModelGroup(brand_id=brand.id, name="Prüfling")
    db_session.add(group)
    db_session.flush()
    variant = ModelVariant(model_group_id=group.id, name="2.0 TDI", model_year_from=2021, vehicle_kind="passenger_car", fuel_type="diesel", body_style="estate", drivetrain="awd", transmission="automatic")
    db_session.add(variant)
    db_session.flush()
    db_session.add_all([
        ModelVariantEnergyRating(model_variant_id=variant.id, rating_year=2024, energy_efficiency_category="b", source="bfe"),
        ModelVariantEnergyRating(model_variant_id=variant.id, rating_year=2026, energy_efficiency_category="c", source="auto_i_dat"),
    ])
    db_session.commit()
    return variant


def test_the_year_is_the_first_registration_then_the_model_year_then_the_newest(db_session):
    variant = _variant(db_session)

    by_registration = energy_rating_for_configuration(db_session, model_variant_id=variant.id, first_registration_year=2024, model_year=2026)
    assert (by_registration.energy_efficiency_category, by_registration.rating_year) == ("b", 2024)
    by_model_year = energy_rating_for_configuration(db_session, model_variant_id=variant.id, first_registration_year=None, model_year=2026)
    assert (by_model_year.energy_efficiency_category, by_model_year.rating_year) == ("c", 2026)
    newest = energy_rating_for_configuration(db_session, model_variant_id=variant.id, first_registration_year=None, model_year=None)
    assert newest.rating_year == 2026
    assert energy_rating_for_configuration(db_session, model_variant_id=variant.id, first_registration_year=2019, model_year=None) is None, "an unrated year is no guess"
    assert energy_rating_for_configuration(db_session, model_variant_id=None, first_registration_year=2024, model_year=None) is None


def test_the_configuration_read_shows_the_label_with_its_year(client, db_session):
    variant = _variant(db_session)
    tenant_id = uuid.uuid4()
    config = configuration_service.create_configuration(
        db_session, tenant_id=tenant_id, actor_id=uuid.uuid4(),
        data=ConfigurationCreate(
            source=ConfigurationSource.PROVIDER, mode=ConfigurationMode.RECORD, match_method=ConfigurationMatchMethod.TYPENSCHEIN,
            catalogue_variant_id=variant.id, first_registration_date="2024-05-01",
        ),
    )
    db_session.commit()
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(), roles=frozenset({AccessRole.SALES}), is_dealer_manager=True)

    response = client.get(f"/v1/configurations/{config.id}", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    assert response.json()["energyRating"] == {"category": "b", "year": 2024, "source": "bfe"}
