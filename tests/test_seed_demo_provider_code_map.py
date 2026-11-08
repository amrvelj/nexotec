"""`scripts/seed_demo_provider_code_map.py` (KAN-548): the demo provider gets
the real provider's code map, idempotently, so the live browse can translate
a vehicle kind for it on a local or staging database."""

from sqlalchemy import func, select

from app.vehicle.models.provider import ProviderCodeMap
from scripts.seed_demo_provider_code_map import DEMO_PROVIDER, REAL_PROVIDER, copy_code_map
from tests.test_provider_code_map_seed import _seed_migration


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
