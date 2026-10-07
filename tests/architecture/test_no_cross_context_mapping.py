"""Architecture test (KAN-84, CLAUDE.md non-negotiable rule 2): no
cross-context foreign keys and no cross-context ORM joins.

import-linter (rule 3) only sees Python import statements. A string passed to
`ForeignKey("table.column")` creates no import edge at all, and a
`relationship()`'s target or `primaryjoin` string is invisible to it too — so
a model in one context can reach into another context's table without the
import-linter ever noticing. This closes that gap from the other side: it
walks SQLAlchemy's own mapped-model metadata (no database, no regex over
source) and asserts:

- the FK gate: every ForeignKey's target table is mapped by a model of the
  same bounded context as the table that declares it;
- the relationship gate: every relationship()'s target class lives in the
  same bounded context as the class that declares it.

Every model module — `app/<context>/models/*.py` and `app/core/*_model.py` —
is imported here first, so the gate never depends on `app.model_registry`
listing it (KAN-84's review found that registry missing one), and a table on
`Base.metadata` that no model maps fails the gate rather than escaping it.

A model's context is the second segment of its module path
(`app.<context>.…`; `app.core.*_model` is the `core` context). A reference
to another context is a plain GUID column plus the three-column label
pattern, never an FK or a relationship.

An exact, per-attribute allowlist — file and attribute, never a directory —
in the same spirit as test_no_ambient_group_read.py's `_ALLOWED_FILES`. It is
empty: KAN-84 replaced the one crossing (`VehicleParty.vehicle`, customer ->
vehicle) with denormalised label columns. A new entry needs an ADR, not a
convenience.
"""

import importlib
from pathlib import Path

from sqlalchemy import Column, ForeignKey, MetaData, Table
from sqlalchemy.orm import Mapper, registry, relationship

import app.model_registry  # noqa: F401  (registers every mapped class on Base)
from app.db import Base

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _REPO_ROOT / "app"


def _model_module_files() -> list[Path]:
    files = [*_APP_ROOT.glob("*/models/*.py"), *(_APP_ROOT / "core").glob("*_model.py")]
    return sorted(f for f in files if f.name != "__init__.py")


def _module_name(file: Path) -> str:
    return ".".join(file.relative_to(_REPO_ROOT).with_suffix("").parts)


for _file in _model_module_files():
    importlib.import_module(_module_name(_file))

# (repo-relative file of the declaring class, attribute name) -> reason.
_ALLOWED_RELATIONSHIPS: dict[tuple[str, str], str] = {}
# (repo-relative file of the declaring class, column name) -> reason.
_ALLOWED_FOREIGN_KEYS: dict[tuple[str, str], str] = {}


def _context_of(cls: type) -> str:
    parts = cls.__module__.split(".")
    assert parts[0] == "app" and len(parts) >= 3, (
        f"{cls.__module__}.{cls.__qualname__} is mapped outside a bounded-context package (app.<context>.…); "
        "the cross-context mapping gate cannot place it"
    )
    return parts[1]


def _file_of(cls: type) -> str:
    return cls.__module__.replace(".", "/") + ".py"


def _table_owners(mappers: list[Mapper]) -> dict[str, type]:
    owners: dict[str, type] = {}
    for mapper in mappers:
        for table in mapper.tables:
            owners.setdefault(table.name, mapper.class_)
    return owners


def find_cross_context_mappings(mappers: list[Mapper]) -> list[str]:
    """Every FK or relationship among `mappers` whose target belongs to a
    different context than its declaring class, minus the allowlists.
    A function of the mappers alone, so the negative case below can run it
    against a throwaway registry."""

    owners = _table_owners(mappers)
    violations: list[str] = []
    for mapper in mappers:
        cls = mapper.class_
        context = _context_of(cls)
        for table in mapper.tables:
            for fk in table.foreign_keys:
                target_table = fk.target_fullname.split(".")[0]
                target_cls = owners.get(target_table)
                key = (_file_of(cls), fk.parent.name)
                if target_cls is None:
                    violations.append(
                        f"FK {table.name}.{fk.parent.name} -> {fk.target_fullname}: target table is not mapped by any "
                        "model, so its owning context cannot be checked"
                    )
                elif _context_of(target_cls) != context and key not in _ALLOWED_FOREIGN_KEYS:
                    violations.append(
                        f"FK {table.name}.{fk.parent.name} ({context}) -> {fk.target_fullname} "
                        f"({_context_of(target_cls)}), declared on {cls.__module__}.{cls.__qualname__}"
                    )
        for rel in mapper.relationships:
            if rel.parent is not mapper:
                continue  # inherited; checked on the class that declares it
            target_cls = rel.mapper.class_
            key = (_file_of(cls), rel.key)
            if _context_of(target_cls) != context and key not in _ALLOWED_RELATIONSHIPS:
                violations.append(
                    f"relationship {cls.__module__}.{cls.__qualname__}.{rel.key} ({context}) -> "
                    f"{target_cls.__module__}.{target_cls.__qualname__} ({_context_of(target_cls)})"
                )
    return violations


def _app_mappers() -> list[Mapper]:
    # tests/demo_models.py maps throwaway fixture classes on the same Base
    # for the core-mechanism tests; they belong to no context.
    mappers = [m for m in Base.registry.mappers if not m.class_.__module__.startswith("tests.")]
    for mapper in mappers:
        # Resolves every string target and primaryjoin now, so a broken one
        # fails here rather than silently looking "unmapped".
        mapper._check_configure()
    return mappers


def test_no_foreign_key_or_relationship_crosses_a_context():
    mappers = _app_mappers()
    mapped_tables = {t.name for m in mappers for t in m.tables}
    # demo_* tables: tests/demo_models.py's fixture models, excluded with them.
    unmapped = sorted(n for n in Base.metadata.tables if n not in mapped_tables and not n.startswith("demo_"))
    violations = [
        f"table {name} is on Base.metadata but no model maps it, so its owning context cannot be checked"
        for name in unmapped
    ] + find_cross_context_mappings(mappers)
    assert not violations, (
        "CLAUDE.md rule 2 — no cross-context foreign keys or joins. Store the other context's id as a plain GUID "
        "column (comment naming the owner) plus a denormalised label and labelRefreshedAt:\n  "
        + "\n  ".join(violations)
    )


def test_the_gate_sees_every_mapped_model():
    """Guards the guard: every model module that defines a mapped class is
    among the mappers walked, so no module — and no context — passes
    vacuously."""

    walked_modules = {m.class_.__module__ for m in _app_mappers()}
    for file in _model_module_files():
        module = importlib.import_module(_module_name(file))
        defines_a_model = any(
            isinstance(v, type) and v.__module__ == module.__name__ and hasattr(v, "__mapper__")
            for v in vars(module).values()
        )
        assert not defines_a_model or module.__name__ in walked_modules, module.__name__
    contexts = {_context_of(m.class_) for m in _app_mappers()}
    assert {"core", "platform", "customer", "vehicle", "sales", "inventory", "valuation", "integration"} <= contexts


def test_allowlist_entries_point_at_real_files():
    for file, _ in [*_ALLOWED_RELATIONSHIPS, *_ALLOWED_FOREIGN_KEYS]:
        assert (_REPO_ROOT / file).is_file(), f"stale allowlist entry: {file}"


# --- the gate actually fails on a crossing ---------------------------------------------


def _synthetic_mappers(*, cross: bool) -> list[Mapper]:
    """Two throwaway classes on their own registry and MetaData (never Base's),
    module-tagged as if they lived in two contexts. `cross=True` gives the
    customer-side class an FK and a relationship into the vehicle-side one."""

    reg = registry(metadata=MetaData())

    class SyntheticVehicle:
        pass

    class SyntheticParty:
        pass

    SyntheticVehicle.__module__ = "app.vehicle.models.synthetic"
    SyntheticParty.__module__ = "app.customer.models.synthetic"

    vehicle_table = Table("synthetic_vehicle", reg.metadata, Column("id", app_guid(), primary_key=True))
    party_columns = [Column("id", app_guid(), primary_key=True)]
    if cross:
        party_columns.append(Column("vehicle_id", app_guid(), ForeignKey("synthetic_vehicle.id")))
    else:
        party_columns.append(Column("vehicle_id", app_guid()))
    party_table = Table("synthetic_party", reg.metadata, *party_columns)

    reg.map_imperatively(SyntheticVehicle, vehicle_table)
    reg.map_imperatively(
        SyntheticParty,
        party_table,
        properties={"vehicle": relationship(SyntheticVehicle, viewonly=True)} if cross else {},
    )
    reg.configure()
    return list(reg.mappers)


def app_guid():
    from app.core.types import GUID

    return GUID()


def test_negative_case_a_cross_context_fk_and_relationship_are_both_caught():
    violations = find_cross_context_mappings(_synthetic_mappers(cross=True))
    assert any(v.startswith("FK synthetic_party.vehicle_id (customer) -> synthetic_vehicle.id (vehicle)") for v in violations)
    assert any(v.startswith("relationship app.customer.models.synthetic") and ".vehicle (customer)" in v for v in violations)
    assert len(violations) == 2


def test_negative_case_control_a_plain_guid_reference_passes():
    assert find_cross_context_mappings(_synthetic_mappers(cross=False)) == []
