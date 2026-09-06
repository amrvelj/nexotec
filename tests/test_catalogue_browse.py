"""Configurator C-B (KAN-40) — catalogue browse and facet search (FR-C-01).

Service-layer tests (drill-down, facets, mode, paging) plus a couple of
end-to-end endpoint checks. The "no provider call" invariant lives in
`tests/architecture/test_catalogue_browse_makes_no_provider_call.py`.
"""

import uuid
from decimal import Decimal

import pytest

from app.core.auth import create_access_token
from app.core.pagination import SortPageParams
from app.core.sorting import SortField
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant, TypeApproval, VariantTypeApproval
from app.vehicle.schemas.catalogue import CatalogueBrowseMode
from app.vehicle.services import catalogue_browse
from app.vehicle.services.catalogue_browse import VariantFilters

# --- fixtures ---------------------------------------------------------


def _enable_browse(db_session, tenant_id: uuid.UUID) -> None:
    """A tenant needs an enabled vehicle-data connection for browse to be
    'available' (PRD: no contract → browse hidden, not broken)."""

    provider = IntegrationProvider(
        provider_code="auto_i_dat_mock",
        category="vehicle_data",
        display_name="auto-i-dat (mock)",
        auth_type="none",
        required_secret_slots=[],
        capability_codes=["vehicle_data"],
    )
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    connection_service.create_connection(
        db_session,
        tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX
        ),
        actor_id=uuid.uuid4(),
    )


def _brand(db_session, code: str, name: str) -> Brand:
    b = Brand(code=code, display_name=name)
    db_session.add(b)
    db_session.flush()
    return b


def _group(db_session, brand: Brand, name: str) -> ModelGroup:
    g = ModelGroup(brand_id=brand.id, name=name)
    db_session.add(g)
    db_session.flush()
    return g


def _variant(db_session, group: ModelGroup, name: str, **spec) -> ModelVariant:
    v = ModelVariant(
        model_group_id=group.id,
        name=name,
        model_year_from=spec.pop("model_year_from", 2022),
        model_year_to=spec.pop("model_year_to", None),
        vehicle_kind=spec.pop("vehicle_kind", "passenger_car"),
        **spec,
    )
    db_session.add(v)
    db_session.flush()
    return v


def _sort(field: str, column, direction: str = "asc", nullable: bool = True) -> SortPageParams:
    return SortPageParams(
        limit=50,
        cursor=None,
        sort_fields=[SortField(api_name=field, column=column, direction=direction, nullable=nullable)],
    )


@pytest.fixture
def catalogue(db_session):
    """A small three-brand catalogue with mixed specs."""

    tenant_id = uuid.uuid4()
    _enable_browse(db_session, tenant_id)

    alfa = _brand(db_session, "alfa", "Alfa Romeo")
    vw = _brand(db_session, "vw", "Volkswagen")
    giulietta = _group(db_session, alfa, "Giulietta")
    golf = _group(db_session, vw, "Golf")

    _variant(db_session, giulietta, "Giulietta 1.4 TB", fuel_type="petrol", body_style="hatchback",
             drivetrain="fwd", transmission="manual", ps=120, base_price=Decimal("28900.00"), model_year_to=2021)
    _variant(db_session, giulietta, "Giulietta 2.0 JTDm", fuel_type="diesel", body_style="hatchback",
             drivetrain="fwd", transmission="automatic", ps=175, base_price=Decimal("33900.00"))
    _variant(db_session, golf, "Golf GTI", fuel_type="petrol", body_style="hatchback",
             drivetrain="fwd", transmission="automatic", ps=245, base_price=Decimal("42500.00"))
    _variant(db_session, golf, "Golf R", fuel_type="petrol", body_style="hatchback",
             drivetrain="awd", transmission="automatic", ps=320, base_price=Decimal("55000.00"))
    db_session.commit()
    return {"tenant_id": tenant_id, "alfa": alfa, "vw": vw, "giulietta": giulietta, "golf": golf}


# --- drill-down ------------------------------------------------------


def test_drill_down_by_brand_then_model_group_narrows_the_results(db_session, catalogue):
    rows, *_ = catalogue_browse.browse_variants(
        db_session, tenant_id=catalogue["tenant_id"], brand_id=catalogue["vw"].id, model_group_id=None,
        filters=VariantFilters(), mode=CatalogueBrowseMode.RECORD, params=_sort("variantName", ModelVariant.name, nullable=False),
    )
    assert {r.name for r in rows} == {"Golf GTI", "Golf R"}

    rows, *_ = catalogue_browse.browse_variants(
        db_session, tenant_id=catalogue["tenant_id"], brand_id=catalogue["vw"].id,
        model_group_id=catalogue["golf"].id, filters=VariantFilters(), mode=CatalogueBrowseMode.RECORD,
        params=_sort("variantName", ModelVariant.name, nullable=False),
    )
    assert {r.name for r in rows} == {"Golf GTI", "Golf R"}


def test_list_model_groups_is_scoped_to_the_brand(db_session, catalogue):
    groups = catalogue_browse.list_model_groups(db_session, brand_id=catalogue["alfa"].id)
    assert [g.name for g in groups] == ["Giulietta"]


# --- facets ---------------------------------------------------------


def test_coded_facet_filter_applies(db_session, catalogue):
    rows, *_ = catalogue_browse.browse_variants(
        db_session, tenant_id=catalogue["tenant_id"], brand_id=None, model_group_id=None,
        filters=VariantFilters(coded={"drivetrain": "awd"}), mode=CatalogueBrowseMode.RECORD,
        params=_sort("variantName", ModelVariant.name, nullable=False),
    )
    assert {r.name for r in rows} == {"Golf R"}


def test_numeric_range_filter_applies(db_session, catalogue):
    rows, *_ = catalogue_browse.browse_variants(
        db_session, tenant_id=catalogue["tenant_id"], brand_id=None, model_group_id=None,
        filters=VariantFilters(numeric_min={"ps": Decimal(200)}, numeric_max={"ps": Decimal(300)}),
        mode=CatalogueBrowseMode.RECORD, params=_sort("ps", ModelVariant.ps),
    )
    assert {r.name for r in rows} == {"Golf GTI"}


def test_facets_report_only_values_that_exist_in_scope_with_counts(db_session, catalogue):
    facets = catalogue_browse.compute_facets(
        db_session, tenant_id=catalogue["tenant_id"], brand_id=catalogue["alfa"].id,
        model_group_id=None, mode=CatalogueBrowseMode.RECORD,
    )
    assert facets.browse_available is True
    fuel = dict(facets.coded["fuelType"])
    assert fuel == {"petrol": 1, "diesel": 1}  # only Giulietta's two, not the Golfs'
    assert "awd" not in dict(facets.coded["drivetrain"])  # awd is a Golf only
    ps_min, ps_max = facets.numeric["ps"]
    assert (ps_min, ps_max) == (120, 175)


# --- mode → production year ----------------------------------------


def test_build_mode_defaults_to_in_production_only(db_session, catalogue):
    rows, *_ = catalogue_browse.browse_variants(
        db_session, tenant_id=catalogue["tenant_id"], brand_id=catalogue["alfa"].id, model_group_id=None,
        filters=VariantFilters(), mode=CatalogueBrowseMode.BUILD,
        params=_sort("variantName", ModelVariant.name, nullable=False),
    )
    # Giulietta 1.4 TB has model_year_to=2021 (out of production) → excluded
    assert {r.name for r in rows} == {"Giulietta 2.0 JTDm"}


def test_record_mode_shows_everything(db_session, catalogue):
    rows, *_ = catalogue_browse.browse_variants(
        db_session, tenant_id=catalogue["tenant_id"], brand_id=catalogue["alfa"].id, model_group_id=None,
        filters=VariantFilters(), mode=CatalogueBrowseMode.RECORD,
        params=_sort("variantName", ModelVariant.name, nullable=False),
    )
    assert {r.name for r in rows} == {"Giulietta 1.4 TB", "Giulietta 2.0 JTDm"}


def test_explicit_in_production_filter_overrides_the_mode_default(db_session, catalogue):
    rows, *_ = catalogue_browse.browse_variants(
        db_session, tenant_id=catalogue["tenant_id"], brand_id=catalogue["alfa"].id, model_group_id=None,
        filters=VariantFilters(in_production=False), mode=CatalogueBrowseMode.BUILD,
        params=_sort("variantName", ModelVariant.name, nullable=False),
    )
    assert {r.name for r in rows} == {"Giulietta 1.4 TB"}


# --- sorting -------------------------------------------------------


def test_sort_by_power_descending(db_session, catalogue):
    rows, *_ = catalogue_browse.browse_variants(
        db_session, tenant_id=catalogue["tenant_id"], brand_id=None, model_group_id=None,
        filters=VariantFilters(), mode=CatalogueBrowseMode.RECORD,
        params=_sort("ps", ModelVariant.ps, direction="desc"),
    )
    assert [r.ps for r in rows] == [320, 245, 175, 120]


# --- no-connection degradation -----------------------------------


def test_browse_is_unavailable_without_a_provider_connection(db_session, catalogue):
    rows, _next_cursor, total, _is_estimate, available = catalogue_browse.browse_variants(
        db_session, tenant_id=uuid.uuid4(), brand_id=None, model_group_id=None,
        filters=VariantFilters(), mode=CatalogueBrowseMode.RECORD,
        params=_sort("variantName", ModelVariant.name, nullable=False),
    )
    assert (rows, total, available) == ([], 0, False)
    facets = catalogue_browse.compute_facets(
        db_session, tenant_id=uuid.uuid4(), brand_id=None, model_group_id=None, mode=CatalogueBrowseMode.RECORD
    )
    assert facets.browse_available is False


# --- typenschein passthrough -----------------------------------


def test_variant_carries_its_typenschein_numbers(db_session, catalogue):
    v = db_session.query(ModelVariant).filter_by(name="Golf GTI").one()
    db_session.add(
        TypeApproval(
            type_approval_number="1AB234",
            variant_links=[VariantTypeApproval(model_variant_id=v.id)],
        )
    )
    db_session.commit()

    rows, *_ = catalogue_browse.browse_variants(
        db_session, tenant_id=catalogue["tenant_id"], brand_id=catalogue["vw"].id,
        model_group_id=catalogue["golf"].id, filters=VariantFilters(coded={"transmission": "automatic"}),
        mode=CatalogueBrowseMode.RECORD, params=_sort("variantName", ModelVariant.name, nullable=False),
    )
    gti = next(r for r in rows if r.name == "Golf GTI")
    assert catalogue_browse.type_approval_numbers_for(gti) == ["1AB234"]


# --- paging at catalogue scale -------------------------------------


def test_paging_holds_at_catalogue_scale(db_session):
    """Exit criterion 4 — 12 000 variants, paged 50 at a time, every id
    returned exactly once, and the footer count switches to an estimate
    above the 10 000 threshold."""

    tenant_id = uuid.uuid4()
    _enable_browse(db_session, tenant_id)
    brand = _brand(db_session, "scale", "Scale Motors")
    group = _group(db_session, brand, "Scale Group")
    total_rows = 12_000
    db_session.bulk_save_objects(
        [
            ModelVariant(model_group_id=group.id, name=f"Scale {i:05d}", model_year_from=2022, vehicle_kind="passenger_car")
            for i in range(total_rows)
        ]
    )
    db_session.commit()

    seen: set[uuid.UUID] = set()
    cursor = None
    pages = 0
    reported_estimate = None
    while True:
        params = SortPageParams(
            limit=50,
            cursor=cursor,
            sort_fields=[SortField(api_name="variantName", column=ModelVariant.name, direction="asc", nullable=False)],
        )
        rows, next_cursor, _total, is_estimate, _available = catalogue_browse.browse_variants(
            db_session, tenant_id=tenant_id, brand_id=brand.id, model_group_id=group.id,
            filters=VariantFilters(), mode=CatalogueBrowseMode.RECORD, params=params,
        )
        reported_estimate = is_estimate
        for r in rows:
            assert r.id not in seen
            seen.add(r.id)
        pages += 1
        if next_cursor is None:
            break
        from app.core.pagination import decode_sort_cursor

        cursor = decode_sort_cursor(next_cursor)

    assert len(seen) == total_rows
    assert pages == total_rows // 50
    assert reported_estimate is True  # 12 000 > count_exact_threshold (10 000)


# --- endpoint smoke ---------------------------------------------


def _bearer(tenant_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(),
        roles=frozenset(), is_dealer_manager=False,
    )
    return {"Authorization": f"Bearer {token}"}


def test_endpoints_return_the_grid_the_facets_and_the_model_groups(client, db_session, catalogue):
    headers = _bearer(catalogue["tenant_id"])

    groups = client.get(f"/v1/catalogue/model-groups?brandId={catalogue['alfa'].id}", headers=headers)
    assert groups.status_code == 200, groups.text
    assert [g["name"] for g in groups.json()["items"]] == ["Giulietta"]

    variants = client.get("/v1/catalogue/variants?mode=record&sort=ps:desc", headers=headers)
    assert variants.status_code == 200, variants.text
    body = variants.json()
    assert body["browseAvailable"] is True
    assert body["total"] == 4
    assert [v["spec"]["ps"] for v in body["items"]] == [320, 245, 175, 120]
    assert body["items"][0]["currentPrice"]["amount"] == "55000.00"

    facets = client.get(f"/v1/catalogue/facets?brandId={catalogue['alfa'].id}&mode=record", headers=headers)
    assert facets.status_code == 200, facets.text
    fuel = {f["valueCode"]: f["count"] for f in facets.json()["coded"]["fuelType"]}
    assert fuel == {"petrol": 1, "diesel": 1}


def test_unknown_sort_field_is_422(client, db_session, catalogue):
    r = client.get("/v1/catalogue/variants?sort=bogus:asc", headers=_bearer(catalogue["tenant_id"]))
    assert r.status_code == 422
