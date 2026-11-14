"""`scripts/seed_demo_provider_code_map.py` (KAN-548): the demo provider gets
the real provider's code map, idempotently, so the live browse can translate
a vehicle kind for it on a local or staging database; and (KAN-649) the
demo provider's open gaps it has just mapped are resolved through
`catalogue_admin.resolve_gap`, filling what they left NULL."""

import uuid

import pytest
from sqlalchemy import func, select

from app.core.audit_model import AuditEvent
from app.platform.reference.models.reference_data import ReferenceList
from app.vehicle.models.provider import MappingGap, ProviderCodeMap
from app.vehicle.services import catalogue_admin
from scripts.seed_demo_provider_code_map import (
    DEMO_PROVIDER,
    REAL_PROVIDER,
    REASON,
    copy_code_map,
    resolve_mapped_demo_gaps,
)
from tests.catalogue_lookup_support import DEMO_FZ_KEYS, see_demo_variant
from tests.test_provider_code_map_seed import _canonical_values, _seed_migration
from tests.test_vehicle_catalogue_sync import _seed_reference_list


def test_the_demo_provider_gets_every_real_row_once(db_session):
    _seed_migration()._seed(db_session.connection())
    db_session.commit()
    real = db_session.scalar(select(func.count()).select_from(ProviderCodeMap).where(ProviderCodeMap.provider == REAL_PROVIDER))
    assert real > 0

    assert copy_code_map(db_session) == real
    demo = db_session.scalars(select(ProviderCodeMap).where(ProviderCodeMap.provider == DEMO_PROVIDER)).all()
    assert len(demo) == real
    kind = next(r for r in demo if r.code_group == "vehicle_kind" and r.canonical_value_code == "passenger_car")
    assert kind.provider_code == "01"

    assert copy_code_map(db_session) == 0, "a second run adds nothing"
    assert db_session.scalar(select(func.count()).select_from(ProviderCodeMap).where(ProviderCodeMap.provider == DEMO_PROVIDER)) == real


# --- KAN-649: the copy also closes the demo provider's gaps it has just mapped ---------------------------


def _seed_catalogue(db_session) -> None:
    """What a migrated database holds: the real provider's seeded code map and
    every canonical list it maps into (each test starts with empty tables)."""

    _seed_migration()._seed(db_session.connection())
    db_session.commit()
    present = set(db_session.scalars(select(ReferenceList.list_code)))
    for list_code, values in sorted(_canonical_values().items()):
        if list_code not in present:
            _seed_reference_list(db_session, list_code, tuple(sorted(values)))


def _open_demo_gaps(db_session) -> list[MappingGap]:
    db_session.expire_all()
    return list(
        db_session.scalars(select(MappingGap).where(MappingGap.provider == DEMO_PROVIDER, MappingGap.resolved.is_(False)))
    )


def test_the_demo_gaps_the_copy_maps_are_resolved_and_their_variants_filled(db_session):
    _seed_catalogue(db_session)
    # Looked up while the demo provider had no code map: every coded field a gap, the variant's value NULL.
    variant = see_demo_variant(db_session, DEMO_FZ_KEYS[0])
    fuel_gap = next(g for g in _open_demo_gaps(db_session) if g.code_group == "fuel_type")
    assert variant.fuel_type is None

    copy_code_map(db_session)
    resolved, refused = resolve_mapped_demo_gaps(db_session)

    assert resolved > 0
    assert refused == 0
    mapped = {
        (row.vehicle_kind, row.code_group, row.provider_code)
        for row in db_session.scalars(select(ProviderCodeMap).where(ProviderCodeMap.provider == DEMO_PROVIDER))
    }
    still_open = _open_demo_gaps(db_session)
    assert not [g for g in still_open if (g.vehicle_kind, g.code_group, g.provider_code) in mapped]
    db_session.refresh(variant)
    assert variant.fuel_type is not None, "the resolve fills what the gap left NULL"
    # Through the production path: one resolve audit row per gap, no invented actor, the script's reason.
    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_type == "vehicle_mapping_gap", AuditEvent.entity_id == fuel_gap.id)
    ).all()
    assert [(a.action, a.actor_id, a.reason) for a in audit] == [("resolve", None, REASON)]
    assert audit[0].after["variantsFilled"] == 1
    db_session.refresh(fuel_gap)
    assert fuel_gap.resolved_by is None
    assert fuel_gap.resolved_value_code == variant.fuel_type


def test_a_second_run_resolves_nothing_and_writes_no_audit_row(db_session):
    _seed_catalogue(db_session)
    see_demo_variant(db_session, DEMO_FZ_KEYS[0])
    copy_code_map(db_session)
    first, _ = resolve_mapped_demo_gaps(db_session)
    audit_rows = db_session.scalar(select(func.count()).select_from(AuditEvent))

    assert copy_code_map(db_session) == 0
    assert resolve_mapped_demo_gaps(db_session) == (0, 0)
    assert first > 0
    assert db_session.scalar(select(func.count()).select_from(AuditEvent)) == audit_rows


def test_a_gap_whose_code_is_still_unmapped_stays_open(db_session):
    # No seed: the copy maps nothing, so the gaps the lookup recorded remain the queue's.
    see_demo_variant(db_session, DEMO_FZ_KEYS[0])
    before = {g.id for g in _open_demo_gaps(db_session)}
    assert before

    assert copy_code_map(db_session) == 0
    assert resolve_mapped_demo_gaps(db_session) == (0, 0)
    assert {g.id for g in _open_demo_gaps(db_session)} == before


def test_a_gap_whose_mapping_the_resolve_refuses_stays_open_and_the_others_resolve(db_session):
    _seed_catalogue(db_session)
    see_demo_variant(db_session, DEMO_FZ_KEYS[0])
    copy_code_map(db_session)
    fuel_gap = next(g for g in _open_demo_gaps(db_session) if g.code_group == "fuel_type")
    # The mapping now names a value its list does not hold (one deactivated since, say).
    mapping = db_session.scalar(
        select(ProviderCodeMap).where(
            ProviderCodeMap.provider == DEMO_PROVIDER, ProviderCodeMap.vehicle_kind == fuel_gap.vehicle_kind,
            ProviderCodeMap.code_group == "fuel_type", ProviderCodeMap.provider_code == fuel_gap.provider_code,
        )
    )
    mapping.canonical_value_code = "no_such_fuel"
    db_session.commit()

    resolved, refused = resolve_mapped_demo_gaps(db_session)

    assert refused == 1
    assert resolved > 0
    assert [g.id for g in _open_demo_gaps(db_session)] == [fuel_gap.id]
    assert db_session.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id == fuel_gap.id)
    ) == 0


def test_a_resolve_without_an_actor_must_name_its_reason(db_session):
    see_demo_variant(db_session, DEMO_FZ_KEYS[0])
    gap = _open_demo_gaps(db_session)[0]

    with pytest.raises(ValueError, match="system_reason"):
        catalogue_admin.resolve_gap(
            db_session, gap=gap, canonical_list_code=gap.code_group, canonical_value_code="x", actor_id=None
        )
    with pytest.raises(ValueError, match="names actor_id"):
        catalogue_admin.resolve_gap(
            db_session, gap=gap, canonical_list_code=gap.code_group, canonical_value_code="x",
            actor_id=uuid.uuid4(), system_reason="a script",
        )
