"""C-B (KAN-40) exit criterion 1 — "Drill-down and facet search both work
against the mirror, with **no provider call** on the browse path — assert
this, do not just avoid it."

Two layers:
  1. Source scan — the browse service and its API import neither
     `app.integration` nor `call_capability` (nor `catalogue_sync`, whose
     only reason to exist is provider calls).
  2. Runtime guard — monkeypatch `app.integration.public.call_capability`
     to blow up, then run `browse_variants` + `compute_facets` and assert
     it is never entered.
"""

import ast
import uuid
from pathlib import Path

import pytest

import app.integration.public as integration_public
from app.core.pagination import SortPageParams
from app.core.sorting import SortField
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant
from app.vehicle.schemas.catalogue import CatalogueBrowseMode
from app.vehicle.services import catalogue_browse
from app.vehicle.services.catalogue_browse import VariantFilters

_REPO = Path(__file__).resolve().parent.parent.parent
_SCANNED = [
    _REPO / "app" / "vehicle" / "services" / "catalogue_browse.py",
    _REPO / "app" / "vehicle" / "api" / "catalogue.py",
]
_FORBIDDEN_IMPORT_PREFIXES = ("app.integration", "app.vehicle.services.catalogue_sync")
_FORBIDDEN_NAMES = {"call_capability", "get_enabled_connection"}


def _imported_modules(tree: ast.AST) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            mods.update(f"{node.module}.{alias.name}" for alias in node.names)
    return mods


def test_the_scanned_files_still_exist():
    for path in _SCANNED:
        assert path.exists(), path


@pytest.mark.parametrize("path", _SCANNED, ids=lambda p: p.name)
def test_browse_source_imports_no_provider_path(path: Path):
    tree = ast.parse(path.read_text())
    mods = _imported_modules(tree)
    offending = [
        m for m in mods if any(m == p or m.startswith(p + ".") for p in _FORBIDDEN_IMPORT_PREFIXES)
    ]
    assert not offending, f"{path.name} imports a provider path: {offending}"

    names = {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Attribute, ast.Name))
    }
    assert not (names & _FORBIDDEN_NAMES), f"{path.name} references {names & _FORBIDDEN_NAMES}"


def test_browse_and_facets_never_reach_call_capability_at_runtime(db_session, monkeypatch):
    def _boom(*_a, **_kw):
        raise AssertionError("catalogue browse must not make a provider call")

    monkeypatch.setattr(integration_public, "call_capability", _boom)

    tenant_id = uuid.uuid4()
    # A connection so browse is 'available' — its mere existence must not
    # trigger a provider call.
    from tests.test_catalogue_browse import _enable_browse

    _enable_browse(db_session, tenant_id)
    brand = Brand(code="x", display_name="X")
    db_session.add(brand)
    db_session.flush()
    group = ModelGroup(brand_id=brand.id, name="G")
    db_session.add(group)
    db_session.flush()
    db_session.add(ModelVariant(model_group_id=group.id, name="V", model_year_from=2022, vehicle_kind="passenger_car"))
    db_session.commit()

    params = SortPageParams(
        limit=50, cursor=None,
        sort_fields=[SortField(api_name="variantName", column=ModelVariant.name, direction="asc", nullable=False)],
    )
    rows, *_ = catalogue_browse.browse_variants(
        db_session, tenant_id=tenant_id, brand_id=brand.id, model_group_id=group.id,
        filters=VariantFilters(), mode=CatalogueBrowseMode.RECORD, params=params,
    )
    assert [r.name for r in rows] == ["V"]

    facets = catalogue_browse.compute_facets(
        db_session, tenant_id=tenant_id, brand_id=brand.id, model_group_id=group.id, mode=CatalogueBrowseMode.RECORD
    )
    assert facets.browse_available is True
