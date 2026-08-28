"""WP-5 PR-2: provider abstraction — resolution and the mapping-gap queue."""

import uuid

from sqlalchemy import select

from app.vehicle.models.provider import MappingGap, ProviderCodeMap
from app.vehicle.services.provider import resolve_mapping_gap, resolve_provider_code


def test_unmapped_code_creates_exactly_one_mapping_gap_row(db_session):
    result = resolve_provider_code(
        db_session, provider="auto-i-dat", vehicle_kind="passenger_car", code_group="011", provider_code="99"
    )
    assert result is None

    # A second, identical miss must not create a second row.
    result_again = resolve_provider_code(
        db_session, provider="auto-i-dat", vehicle_kind="passenger_car", code_group="011", provider_code="99"
    )
    assert result_again is None

    gaps = db_session.scalars(select(MappingGap).where(MappingGap.provider_code == "99")).all()
    assert len(gaps) == 1
    assert gaps[0].occurrences == 2


def test_same_provider_code_maps_differently_per_vehicle_kind(db_session):
    # Treibstoff=3: Diesel for a car, Bleifrei Kat. for a motorcycle — the
    # PRD's own headline example for why vehicle_kind is part of the key.
    db_session.add(
        ProviderCodeMap(
            provider="auto-i-dat",
            vehicle_kind="passenger_car",
            code_group="011",
            provider_code="3",
            canonical_list_code="fuel_type",
            canonical_value_code="diesel",
        )
    )
    db_session.add(
        ProviderCodeMap(
            provider="auto-i-dat",
            vehicle_kind="motorcycle",
            code_group="111",
            provider_code="3",
            canonical_list_code="fuel_type",
            canonical_value_code="unleaded_cat",
        )
    )
    db_session.flush()

    car_result = resolve_provider_code(
        db_session, provider="auto-i-dat", vehicle_kind="passenger_car", code_group="011", provider_code="3"
    )
    moto_result = resolve_provider_code(
        db_session, provider="auto-i-dat", vehicle_kind="motorcycle", code_group="111", provider_code="3"
    )

    assert car_result is not None and car_result.value_code == "diesel"
    assert moto_result is not None and moto_result.value_code == "unleaded_cat"


def test_resolving_a_mapping_gap_creates_the_code_map_row(db_session):
    resolve_provider_code(
        db_session, provider="auto-i-dat", vehicle_kind="passenger_car", code_group="011", provider_code="12"
    )
    gap = db_session.scalar(select(MappingGap).where(MappingGap.provider_code == "12"))
    assert gap is not None and gap.resolved is False

    resolve_mapping_gap(
        db_session,
        gap=gap,
        canonical_list_code="fuel_type",
        canonical_value_code="plugin_hybrid",
        actor_id=uuid.uuid4(),
    )
    assert gap.resolved is True
    assert gap.resolved_value_code == "plugin_hybrid"

    # The same code must now resolve directly, never surfacing as a gap again.
    result = resolve_provider_code(
        db_session, provider="auto-i-dat", vehicle_kind="passenger_car", code_group="011", provider_code="12"
    )
    assert result is not None and result.value_code == "plugin_hybrid"

    unresolved_gaps = db_session.scalars(select(MappingGap).where(MappingGap.resolved.is_(False))).all()
    assert not any(g.provider_code == "12" for g in unresolved_gaps)
