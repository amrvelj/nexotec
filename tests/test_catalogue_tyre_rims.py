"""KAN-596 (FR-C-08; ADR-141): the identAPI's rim data is stored with each
tyre row, one row per approved wheel (axle, season, dimension, rim), and
read by the specification and by the configuration read the stock car's
technical data shows. Over the real identAPI adapter and the fake transport."""

from __future__ import annotations

import types
from decimal import Decimal

from app.integration.adapters import identapi_parse as parse
from app.vehicle.api.configuration import _catalogue_tyre_specs
from app.vehicle.models.catalogue_mirror import TyreSpecCache
from app.vehicle.services import catalogue_entitlements
from tests import identapi_fixtures as fix
from tests.identapi_support import FakeAutoi
from tests.postgres_only import NEEDS_ITS_OWN_CONNECTION
from tests.test_catalogue_details_on_open import _connected, _fresh_adapter_state, _open

_fresh = _fresh_adapter_state


def _tyres(rows: list[dict]) -> dict:
    body = fix.tyres_body()
    body["data"]["values"] = rows
    return body


def _row(**overrides) -> dict:
    return {**fix.tyres_body()["data"]["values"][0], **overrides}


def test_the_parser_reads_the_rim_and_the_factory_fitment():
    [tyre] = parse.parse_tyre_specs(_tyres([_row(standardTyres=0)]))

    assert (tyre.rims, tyre.rim_width, tyre.rim_size, tyre.rim_offset, tyre.is_standard) == (
        "7.5Jx18", Decimal("7.5"), 18, 39, False,
    )
    [unknown] = parse.parse_tyre_specs(_tyres([_row(standardTyres=None, rims=None, rimWidth=None, rimSize=0)]))
    assert (unknown.rims, unknown.rim_width, unknown.rim_size, unknown.is_standard) == (None, None, None, None)


@NEEDS_ITS_OWN_CONNECTION
def test_every_approved_wheel_of_an_axle_and_season_is_kept_with_its_rim(db_session, monkeypatch):
    bodies = fix.detail_bodies()
    bodies[fix.TYRES_PATH] = _tyres(
        [
            _row(dimension="205/60 R16", rims="6.5Jx16", rimWidth=6.5, rimSize=16, rimOffset=46, standardTyres=1),
            _row(dimension="225/45 R18"),  # the same axle and season, another wheel: a row of its own
            _row(dimension="225/45 R18", gearbox=7),  # the same wheel for another gearbox: one row
        ]
    )
    s, variant = _connected(db_session, monkeypatch, FakeAutoi(api_bodies=bodies))

    _open(db_session, s, variant)

    rows = (
        db_session.query(TyreSpecCache)
        .filter_by(tenant_id=s.connection.tenant_id, model_variant_id=variant.id)
        .order_by(TyreSpecCache.size)
        .all()
    )
    assert [(r.size, r.rims, r.rim_width, r.rim_size, r.rim_offset, r.is_standard) for r in rows] == [
        ("205/60 R16", "6.5Jx16", Decimal("6.5"), 16, 46, True),
        ("225/45 R18", "7.5Jx18", Decimal("7.5"), 18, 39, True),
    ]

    spec = catalogue_entitlements.get_catalogue_specification(
        db_session, tenant_id=s.connection.tenant_id, model_variant_id=variant.id, load_on_open=False
    )
    assert [(t.size, t.rims, t.rim_size) for t in spec.tyre_specs] == [
        ("205/60 R16", "6.5Jx16", 16),
        ("225/45 R18", "7.5Jx18", 18),
    ]

    config = types.SimpleNamespace(tenant_id=s.connection.tenant_id, catalogue_variant_id=variant.id)
    read = [t.model_dump(by_alias=True) for t in _catalogue_tyre_specs(db_session, config)]
    assert [(t["size"], t["rims"], t["rimWidth"], t["rimOffset"], t["isStandard"]) for t in read] == [
        ("205/60 R16", "6.5Jx16", Decimal("6.5"), 46, True),
        ("225/45 R18", "7.5Jx18", Decimal("7.5"), 39, True),
    ]
    # Another Betrieb's configuration of the same car reads its own rows: none here (ADR-013).
    other = types.SimpleNamespace(tenant_id=fix_other_tenant(), catalogue_variant_id=variant.id)
    assert _catalogue_tyre_specs(db_session, other) == []


def fix_other_tenant():
    import uuid

    return uuid.uuid4()
