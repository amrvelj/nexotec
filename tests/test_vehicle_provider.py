"""WP-5 PR-2: provider abstraction — resolution and the mapping-gap queue."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.errors import ConflictError, UnprocessableEntityError
from app.platform.models.reference_data import ReferenceList, ReferenceValue
from app.vehicle.models.provider import MappingGap, ProviderCodeMap
from app.vehicle.services import provider as provider_service
from app.vehicle.services.provider import resolve_mapping_gap, resolve_provider_code

# The active values of the seeded `fuel_type` list (platform seed c9654d846ac9).
FUEL_TYPES = ("petrol", "diesel", "electric", "hybrid", "plugin_hybrid", "hydrogen")


def _seed_list(db_session, list_code: str, values: tuple[str, ...], inactive: tuple[str, ...] = ()) -> None:
    ref_list = ReferenceList(list_code=list_code)
    db_session.add(ref_list)
    db_session.flush()
    for code in values + inactive:
        db_session.add(
            ReferenceValue(
                list_id=ref_list.id, value_code=code, label_de=code, label_fr=code, label_it=code, label_en=code,
                active=code not in inactive,
            )
        )
    db_session.commit()


def _open_gap(db_session, provider_code: str, *, code_group: str = "fuel_type") -> MappingGap:
    resolve_provider_code(
        db_session, provider="auto_i_dat", vehicle_kind="01", code_group=code_group, provider_code=provider_code
    )
    gap = db_session.scalar(select(MappingGap).where(MappingGap.provider_code == provider_code))
    assert gap is not None and gap.resolved is False
    return gap


def test_unmapped_code_creates_exactly_one_mapping_gap_row(db_session):
    result = resolve_provider_code(
        db_session, provider="auto_i_dat", vehicle_kind="01", code_group="fuel_type", provider_code="99"
    )
    assert result is None

    # A second, identical miss must not create a second row.
    result_again = resolve_provider_code(
        db_session, provider="auto_i_dat", vehicle_kind="01", code_group="fuel_type", provider_code="99"
    )
    assert result_again is None

    gaps = db_session.scalars(select(MappingGap).where(MappingGap.provider_code == "99")).all()
    assert len(gaps) == 1
    assert gaps[0].occurrences == 2


def test_same_provider_code_maps_differently_per_vehicle_kind(db_session):
    # Treibstoff=3: Diesel for a car (CodeGrpNr 011), petrol for a motorcycle (111) — the
    # PRD's own headline example for why vehicle_kind is part of the key.
    db_session.add(
        ProviderCodeMap(
            provider="auto_i_dat",
            vehicle_kind="01",
            code_group="fuel_type",
            provider_code="3",
            canonical_list_code="fuel_type",
            canonical_value_code="diesel",
        )
    )
    db_session.add(
        ProviderCodeMap(
            provider="auto_i_dat",
            vehicle_kind="03",
            code_group="fuel_type",
            provider_code="3",
            canonical_list_code="fuel_type",
            canonical_value_code="petrol",
        )
    )
    db_session.flush()

    car_result = resolve_provider_code(
        db_session, provider="auto_i_dat", vehicle_kind="01", code_group="fuel_type", provider_code="3"
    )
    moto_result = resolve_provider_code(
        db_session, provider="auto_i_dat", vehicle_kind="03", code_group="fuel_type", provider_code="3"
    )

    assert car_result is not None and car_result.value_code == "diesel"
    assert moto_result is not None and moto_result.value_code == "petrol"


def test_resolving_a_mapping_gap_creates_the_code_map_row(db_session):
    _seed_list(db_session, "fuel_type", FUEL_TYPES)
    resolve_provider_code(
        db_session, provider="auto_i_dat", vehicle_kind="01", code_group="fuel_type", provider_code="12"
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
        db_session, provider="auto_i_dat", vehicle_kind="01", code_group="fuel_type", provider_code="12"
    )
    assert result is not None and result.value_code == "plugin_hybrid"

    unresolved_gaps = db_session.scalars(select(MappingGap).where(MappingGap.resolved.is_(False))).all()
    assert not any(g.provider_code == "12" for g in unresolved_gaps)


# --- KAN-77: validated, idempotent resolve ------------------------------------


def test_resolve_records_who_created_the_code_map_row(db_session):
    _seed_list(db_session, "fuel_type", FUEL_TYPES)
    gap = _open_gap(db_session, "12")
    actor_id = uuid.uuid4()

    assert resolve_mapping_gap(
        db_session, gap=gap, canonical_list_code="fuel_type", canonical_value_code="plugin_hybrid", actor_id=actor_id
    ) is True

    row = db_session.scalar(select(ProviderCodeMap).where(ProviderCodeMap.provider_code == "12"))
    assert row is not None and row.created_by == actor_id


def test_resolving_again_with_the_same_target_changes_nothing(db_session):
    _seed_list(db_session, "fuel_type", FUEL_TYPES)
    gap = _open_gap(db_session, "12")
    resolve_mapping_gap(
        db_session, gap=gap, canonical_list_code="fuel_type", canonical_value_code="plugin_hybrid", actor_id=uuid.uuid4()
    )
    resolved_at = gap.resolved_at

    assert resolve_mapping_gap(
        db_session, gap=gap, canonical_list_code="fuel_type", canonical_value_code="plugin_hybrid", actor_id=uuid.uuid4()
    ) is False
    assert gap.resolved_at == resolved_at
    assert len(db_session.scalars(select(ProviderCodeMap).where(ProviderCodeMap.provider_code == "12")).all()) == 1


def test_resolving_again_with_a_different_target_is_a_conflict_naming_the_mapping(db_session):
    _seed_list(db_session, "fuel_type", FUEL_TYPES)
    gap = _open_gap(db_session, "12")
    resolve_mapping_gap(
        db_session, gap=gap, canonical_list_code="fuel_type", canonical_value_code="plugin_hybrid", actor_id=uuid.uuid4()
    )

    with pytest.raises(ConflictError) as exc:
        resolve_mapping_gap(
            db_session, gap=gap, canonical_list_code="fuel_type", canonical_value_code="diesel", actor_id=uuid.uuid4()
        )
    assert "fuel_type/plugin_hybrid" in exc.value.message
    assert gap.resolved_value_code == "plugin_hybrid"


def test_an_open_gap_whose_key_was_mapped_since_closes_against_the_existing_row(db_session):
    # The gap was recorded before migration 7c4e9a2b6d13 seeded its key.
    _seed_list(db_session, "fuel_type", FUEL_TYPES)
    gap = _open_gap(db_session, "3")
    db_session.add(
        ProviderCodeMap(
            provider="auto_i_dat", vehicle_kind="01", code_group="fuel_type", provider_code="3",
            canonical_list_code="fuel_type", canonical_value_code="diesel",
        )
    )
    db_session.flush()

    with pytest.raises(ConflictError):
        resolve_mapping_gap(
            db_session, gap=gap, canonical_list_code="fuel_type", canonical_value_code="petrol", actor_id=uuid.uuid4()
        )
    assert gap.resolved is False

    assert resolve_mapping_gap(
        db_session, gap=gap, canonical_list_code="fuel_type", canonical_value_code="diesel", actor_id=uuid.uuid4()
    ) is True
    assert gap.resolved is True
    assert len(db_session.scalars(select(ProviderCodeMap).where(ProviderCodeMap.provider_code == "3")).all()) == 1


def test_a_resolve_that_loses_the_race_answers_from_the_winning_row(db_session, engine, monkeypatch):
    """Two admins resolve the same key at once: both see no mapping, the other
    commits first, and this insert hits `uq_vehicle_provider_code_map_natural_key`.
    The IntegrityError is the backstop, never an unhandled 500.
    """

    _seed_list(db_session, "fuel_type", FUEL_TYPES)
    gap = _open_gap(db_session, "12")
    db_session.commit()

    other = sessionmaker(bind=engine)()
    try:
        other.add(
            ProviderCodeMap(
                provider="auto_i_dat", vehicle_kind="01", code_group="fuel_type", provider_code="12",
                canonical_list_code="fuel_type", canonical_value_code="plugin_hybrid",
            )
        )
        other.commit()
    finally:
        other.close()

    real_find = provider_service._find_code_map
    calls = {"n": 0}

    def _stale_first_read(db, g):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_find(db, g)

    monkeypatch.setattr(provider_service, "_find_code_map", _stale_first_read)

    with pytest.raises(ConflictError):
        resolve_mapping_gap(
            db_session, gap=gap, canonical_list_code="fuel_type", canonical_value_code="diesel", actor_id=uuid.uuid4()
        )

    calls["n"] = 0
    assert resolve_mapping_gap(
        db_session, gap=gap, canonical_list_code="fuel_type", canonical_value_code="plugin_hybrid", actor_id=uuid.uuid4()
    ) is True
    assert gap.resolved is True


@pytest.mark.parametrize(
    ("list_code", "value_code"),
    [
        ("transmission", "manual"),  # a real list, but not this gap's own
        ("fuel_type", "unicorn"),  # not in the list at all
        ("fuel_type", "lpg"),  # in the list, but deactivated
    ],
)
def test_resolve_rejects_a_target_outside_the_gaps_own_active_list(db_session, list_code, value_code):
    _seed_list(db_session, "fuel_type", FUEL_TYPES, inactive=("lpg",))
    _seed_list(db_session, "transmission", ("manual", "automatic"))
    gap = _open_gap(db_session, "12")

    with pytest.raises(UnprocessableEntityError):
        resolve_mapping_gap(
            db_session, gap=gap, canonical_list_code=list_code, canonical_value_code=value_code, actor_id=uuid.uuid4()
        )
    assert gap.resolved is False
    assert db_session.scalar(select(ProviderCodeMap).where(ProviderCodeMap.provider_code == "12")) is None


def test_a_gap_whose_code_group_has_no_reference_list_is_refused_not_a_crash(db_session):
    # Gaps recorded before the semantic keys carry a numeric CodeGrpNr.
    gap = _open_gap(db_session, "7", code_group="011")

    with pytest.raises(ConflictError) as exc:
        resolve_mapping_gap(
            db_session, gap=gap, canonical_list_code="011", canonical_value_code="petrol", actor_id=uuid.uuid4()
        )
    assert "no reference list" in exc.value.message
    assert gap.resolved is False
