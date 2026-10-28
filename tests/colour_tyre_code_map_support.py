"""KAN-75: the FarbArt / AchsenCode / PneuTyp code-map seed, for tests.

Tests build the schema with ``create_all``, never Alembic, so — like
``test_provider_code_map_seed.py`` — they load the migration by path and run
its own ``_seed`` against the test schema. Opt-in per test, never autouse:
``test_vehicle_catalogue_sync`` relies on an empty map.
"""

import importlib.util
from pathlib import Path

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def code_map_migration():
    (path,) = sorted((_VERSIONS / "vehicle").glob("b0a125a0a583_*.py"))
    return _load(path, "kan75_colour_tyre_code_map")


def reference_values_migration():
    (path,) = sorted((_VERSIONS / "platform").glob("b13e074523d7_*.py"))
    return _load(path, "kan75_colour_area_list")


def seed_colour_tyre_code_map(db_session) -> None:
    code_map_migration()._seed(db_session.connection())
    db_session.commit()
