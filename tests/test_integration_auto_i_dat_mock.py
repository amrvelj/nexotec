"""MockAutoIDatAdapter — the invariants a consumer would otherwise get wrong
(the demo provider's fixtures are demo data; asserting that a fixture returns
what was just written into it pins nothing).

Since KAN-550 the mock speaks the identAPI only: a Wechselschild is several
Vehicle Build rows, and the demo variants carry auto-i's own codes as the
identAPI sends them, so the code map seeded for `auto_i_dat` (copied to the
demo provider by `scripts/seed_demo_provider_code_map.py`) resolves every
coded field — the SOAP-era vehicle kind `1` and gearbox `A`/`M` left with the
SOAP shapes.
"""

import importlib.util
from pathlib import Path

from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter

_DEMO_FZ_KEYS = ("FZ100001", "FZ100002", "FZ100003")
# VariantMasterData attribute -> the code map's code_group (catalogue_sync's keying).
_CODED_FIELDS = {
    "fuel_type_code": "fuel_type",
    "body_style_code": "body_style",
    "drivetrain_code": "drivetrain",
    "transmission_code": "transmission",
    "consumption_norm_code": "consumption_norm",
}
# The same fields as the identAPI's caption groups name them.
_CAPTION_GROUPS = {
    "vehicle_kind_code": "type",
    "fuel_type_code": "fuel",
    "body_style_code": "body",
    "drivetrain_code": "drive",
    "transmission_code": "gearbox",
}


def _seeded_code_map() -> set[tuple[str, str, str]]:
    versions = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "vehicle"
    (path,) = sorted(versions.glob("7c4e9a2b6d13_*.py"))
    spec = importlib.util.spec_from_file_location("kan38_pr2c_code_map_seed", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {(kind, code_group, code) for kind, code_group, code, _list, _value in module.SEED_ROWS}


def _demo_variants():
    adapter = MockAutoIDatAdapter()
    variants = [adapter.fetch_variant(fz_key) for fz_key in _DEMO_FZ_KEYS]
    assert all(v is not None for v in variants)
    return variants


def test_a_wechselschild_plate_is_two_built_vehicles():
    """A plate can name more than one vehicle (Wechselschild), so the answer
    is a list and a caller must never assume one row."""

    adapter = MockAutoIDatAdapter()
    assert len(adapter.identify_by_plate("ZH123456")) == 1

    wechselschild = adapter.identify_by_plate("ZH999999")
    assert len(wechselschild) == 2
    assert {p.brand_name for p in wechselschild} == {"Alfa Romeo", "Volkswagen"}


def test_every_demo_code_resolves_through_the_seeded_code_map():
    seeded = _seeded_code_map()
    unmapped = []
    for variant in _demo_variants():
        kind = variant.vehicle_kind_code
        if (kind, "vehicle_kind", kind) not in seeded:
            unmapped.append((variant.fz_key, "vehicle_kind", kind))
        for attribute, code_group in _CODED_FIELDS.items():
            code = getattr(variant, attribute)
            if code is not None and (kind, code_group, code) not in seeded:
                unmapped.append((variant.fz_key, code_group, code))
    assert unmapped == []


def test_every_demo_code_has_a_caption():
    captions = {(c.group, c.code) for c in MockAutoIDatAdapter().fetch_captions("de")}
    missing = [
        (variant.fz_key, group, getattr(variant, attribute))
        for variant in _demo_variants()
        for attribute, group in _CAPTION_GROUPS.items()
        if getattr(variant, attribute) is not None and (group, getattr(variant, attribute)) not in captions
    ]
    assert missing == []
