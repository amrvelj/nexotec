"""WP-6 PR-4: the catalogue's master-data store and the code-map repairs.
Since KAN-485 (2026-10-29) a variant reaches the catalogue only by a single
lookup — no full seed, no daily delta — and since KAN-211 its licensed
details load once per Betrieb when the car is opened
(`tests/test_catalogue_details_on_open.py`). The A-12 sync-age alarm and the
platform's sync-status board went with the seed (KAN-497).
"""

import dataclasses
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.audit import list_audit_events
from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.platform.reference.models.reference_data import ReferenceList, ReferenceValue
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant, VariantOption, VariantOptionEquipmentFeature
from app.vehicle.models.catalogue_mirror import ColourCache, ImageRef, TyreSpecCache
from app.vehicle.models.provider import MappingGap, ProviderCodeMap, VariantOptionProviderCode, VariantProviderCode
from app.vehicle.services import catalogue_admin, catalogue_sync
from app.vehicle.services.provider import resolve_provider_code
from tests.catalogue_lookup_support import look_up_demo_variants
from tests.postgres_only import NEEDS_ITS_OWN_CONNECTION


def _bare_variant(db_session, name: str = "Bare Variant") -> ModelVariant:
    brand = Brand(code=f"b-{uuid.uuid4().hex[:8]}", display_name="Brand")
    db_session.add(brand)
    db_session.flush()
    group = ModelGroup(brand_id=brand.id, name="Group")
    db_session.add(group)
    db_session.flush()
    variant = ModelVariant(model_group_id=group.id, name=name, model_year_from=2022)
    db_session.add(variant)
    db_session.flush()
    return variant


def _make_mock_provider(db_session) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code="auto_i_dat_mock",
        category="vehicle_data",
        display_name="auto-i-dat (mock)",
        auth_type="none",
        required_secret_slots=[],
        capability_codes=["vehicle_data", "images", "packages", "valuation", "forecast"],
    )
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


def _make_connection(db_session, provider, *, tenant_id=None):
    return connection_service.create_connection(
        db_session,
        tenant_id=tenant_id or uuid.uuid4(),
        data=ConnectionCreate(
            provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX
        ),
        actor_id=uuid.uuid4(),
    )


# --- single lookup ------------------------------------------------------


@NEEDS_ITS_OWN_CONNECTION
def test_a_repeated_lookup_duplicates_neither_variants_nor_tenant_content(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    look_up_demo_variants(db_session, tenant_id)
    look_up_demo_variants(db_session, tenant_id)

    assert db_session.query(ModelVariant).count() == 3  # never duplicated across two rounds of lookups
    assert db_session.query(ColourCache).filter_by(tenant_id=tenant_id).count() == 3 * 3


@NEEDS_ITS_OWN_CONNECTION
def test_a_lookup_persists_the_variant_base_price_the_adapter_already_returns(db_session):
    """C-A: `VariantMasterData.base_price` (LetzterNP) was being dropped —
    the adapter returns it, nothing persisted it. KAN-547: the price's year
    and its net flag arrive with the variant and are stored beside it."""

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    look_up_demo_variants(db_session, tenant_id)

    priced = [v for v in db_session.query(ModelVariant).all() if v.base_price is not None]
    assert len(priced) == 3
    assert {str(v.base_price) for v in priced} == {"28900.00", "42500.00", "54900.00"}
    assert {(v.base_price_year, v.price_is_net) for v in priced} == {(2021, False), (2023, False)}


@NEEDS_ITS_OWN_CONNECTION
def test_a_lookup_writes_a_mapping_gap_on_an_unresolved_provider_code(db_session):
    """No ProviderCodeMap rows exist for `auto_i_dat_mock` in this test —
    every *_code field on every demo variant misses, and the existing
    WP-5 mapping-gap machinery (never rebuilt, just called) writes one row
    per distinct (provider, vehicle_kind, code_group, provider_code), with
    occurrences bumped on repeats rather than duplicated.
    """

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    look_up_demo_variants(db_session, tenant_id)

    gaps = db_session.query(MappingGap).filter_by(provider="auto_i_dat_mock").all()
    assert len(gaps) > 0
    assert all(gap.resolved is False for gap in gaps)
    # Every demo variant shares vehicle_kind_code="01" (see auto_i_dat_mock.py's
    # own fixture data) — the vehicle_kind gap for provider_code "01" is hit
    # by all three, so its occurrences count reflects that, never three
    # separate rows for the identical miss.
    vehicle_kind_gap = next(g for g in gaps if g.code_group == "vehicle_kind" and g.provider_code == "01")
    # Each demo variant is visited twice: stored as seen, then its colours
    # request on open carries the master data again (KAN-211) — a revisit
    # bumps the gap like any other miss of the same code.
    assert vehicle_kind_gap.occurrences == 6


# --- KAN-43 (C-E): options/colours/tyres field wiring --------------------


@NEEDS_ITS_OWN_CONNECTION
def test_a_lookup_populates_option_included_package_and_model_year(db_session):
    """`is_included` / `is_package` / `model_year` were added to
    `VariantOption` by C-A but never populated by this sync until now."""

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    look_up_demo_variants(db_session, tenant_id)

    golf = db_session.query(ModelVariant).filter_by(name="Golf GTI 2.0 TSI DSG").one()
    options = {
        o.option_code: o
        for o in db_session.query(VariantOption).filter_by(tenant_id=tenant_id, model_variant_id=golf.id).all()
    }
    assert options["WNTR"].is_package is True
    assert options["WNTR"].is_included is False
    assert options["AC"].is_included is True
    assert options["AC"].is_package is False
    # KAN-211: a car still in production carries the provider's "currently
    # available" equipment — the current model year, not its first one.
    from app.vehicle.services.plate import swiss_today

    assert golf.model_year_to is None
    assert all(o.model_year == swiss_today().year for o in options.values())


@NEEDS_ITS_OWN_CONNECTION
def test_a_lookup_resolves_equipment_features_via_provider_code_map(db_session):
    """`SuchCode` resolves through the same `resolve_provider_code`/
    `MappingGap` machinery every other coded field uses — a mapped code
    becomes a `VariantOptionEquipmentFeature` row, an unmapped one is left
    off the option and surfaces as a gap instead (FR-C-06/ADR-072's own
    "never silently dropped" posture, applied here to SuchCode)."""

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)
    db_session.add(
        ProviderCodeMap(
            provider="auto_i_dat_mock",
            vehicle_kind="01",
            code_group="equipment_feature",
            provider_code="navigation",
            canonical_list_code="equipment_feature",
            canonical_value_code="navigation",
        )
    )
    db_session.commit()

    look_up_demo_variants(db_session, tenant_id)

    golf = db_session.query(ModelVariant).filter_by(name="Golf GTI 2.0 TSI DSG").one()
    nav = (
        db_session.query(VariantOption)
        .filter_by(tenant_id=tenant_id, model_variant_id=golf.id, option_code="NAV")
        .one()
    )
    feature_codes = {link.feature_value_code for link in nav.equipment_feature_links}
    # "navigation" resolves (mapped above); "APPLE_CARPLAY" does not (no
    # mapping row exists for it) and is left off rather than stored raw.
    assert feature_codes == {"navigation"}

    gap = (
        db_session.query(MappingGap)
        .filter_by(provider="auto_i_dat_mock", code_group="equipment_feature", provider_code="APPLE_CARPLAY")
        .one()
    )
    assert gap.resolved is False


def test_sync_option_equipment_features_replaces_the_set_on_each_call(db_session):
    """Unlike every other field in this sync (upsert-and-update-in-place,
    never delete), an option's equipment-feature set is genuinely
    many-valued: a feature the provider stops returning for an option must
    disappear locally too, or a corrected SuchCode mapping would only ever
    grow the set."""

    tenant_id = uuid.uuid4()
    variant = _bare_variant(db_session)
    option = VariantOption(
        tenant_id=tenant_id, model_variant_id=variant.id, option_code="NAV", description="Navigation"
    )
    db_session.add(option)
    db_session.flush()
    db_session.add(
        ProviderCodeMap(
            provider="auto_i_dat_mock",
            vehicle_kind="1",
            code_group="equipment_feature",
            provider_code="navigation",
            canonical_list_code="equipment_feature",
            canonical_value_code="navigation",
        )
    )
    db_session.commit()

    catalogue_sync.sync_option_equipment_features(
        db_session,
        tenant_id=tenant_id,
        provider_code="auto_i_dat_mock",
        vehicle_kind_code="1",
        variant_option=option,
        feature_codes=["navigation"],
    )
    db_session.commit()
    codes = {
        r.feature_value_code
        for r in db_session.query(VariantOptionEquipmentFeature).filter_by(variant_option_id=option.id)
    }
    assert codes == {"navigation"}

    catalogue_sync.sync_option_equipment_features(
        db_session,
        tenant_id=tenant_id,
        provider_code="auto_i_dat_mock",
        vehicle_kind_code="1",
        variant_option=option,
        feature_codes=[],
    )
    db_session.commit()
    codes = {
        r.feature_value_code
        for r in db_session.query(VariantOptionEquipmentFeature).filter_by(variant_option_id=option.id)
    }
    assert codes == set()


@NEEDS_ITS_OWN_CONNECTION
def test_a_lookup_populates_colour_surcharge_price(db_session):
    """FR-C-07: "the surcharge is a price line in build mode." `Preis`
    was on the raw response but parsed by no one until now."""

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    look_up_demo_variants(db_session, tenant_id)

    golf = db_session.query(ModelVariant).filter_by(name="Golf GTI 2.0 TSI DSG").one()
    colours = {
        c.colour_code: c
        for c in db_session.query(ColourCache).filter_by(tenant_id=tenant_id, model_variant_id=golf.id).all()
    }
    assert colours["BLK"].price == Decimal("0.00")
    assert colours["RED"].price == Decimal("1100.00")
    assert colours["GRY"].price is None  # interior colour, no surcharge in the fixture


@NEEDS_ITS_OWN_CONNECTION
def test_a_lookup_populates_tyre_remark_and_season(db_session):
    """FR-C-08: BemDe ("nur mit Leichtmetallfelgen") and PneuTyp
    (summer/winter) must be shown with the dimension."""

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    look_up_demo_variants(db_session, tenant_id)

    golf = db_session.query(ModelVariant).filter_by(name="Golf GTI 2.0 TSI DSG").one()
    # Keyed by the raw AchsenCode / PneuTyp (KAN-75); this module's code map
    # is empty, so they resolve to nothing here (tests/test_kan75_colour_tyre_code_map.py).
    specs = {
        (t.axle_code, t.season_code): t
        for t in db_session.query(TyreSpecCache).filter_by(tenant_id=tenant_id, model_variant_id=golf.id).all()
    }
    assert specs[("2", "121")].remark == "nur mit Leichtmetallfelgen"
    assert specs[("3", "121")].remark == "nur mit Leichtmetallfelgen"
    assert specs[("1", "122")].remark is None


@NEEDS_ITS_OWN_CONNECTION
def test_a_lookup_populates_the_full_image_url(db_session):
    """FR-C-09 — the full BildURL, previously discarded at ingestion
    (an explicit WP-6 Open Item); without it no caller can render a photo,
    only reference an opaque key."""

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    look_up_demo_variants(db_session, tenant_id)

    golf = db_session.query(ModelVariant).filter_by(name="Golf GTI 2.0 TSI DSG").one()
    front = (
        db_session.query(ImageRef)
        .filter_by(tenant_id=tenant_id, model_variant_id=golf.id, image_key="FZ100002-front.jpg")
        .one()
    )
    assert front.image_url == "https://images.autoi.ch/img/FZ100002-front.jpg"
    interior = (
        db_session.query(ImageRef)
        .filter_by(tenant_id=tenant_id, model_variant_id=golf.id, image_key="FZ100002-interior.jpg")
        .one()
    )
    assert interior.image_url == "https://images.autoi.ch/img/FZ100002-interior.jpg"


def _tyre(tenant_id, variant, axle_code: str, season_code: str, size: str) -> TyreSpecCache:
    return TyreSpecCache(
        tenant_id=tenant_id, model_variant_id=variant.id, axle_code=axle_code, season_code=season_code, size=size
    )


def test_tyre_spec_cache_allows_the_same_axle_with_two_different_seasons(db_session):
    """The bug KAN-43 fixed: the original unique constraint keyed only
    on (tenant, variant, axle), so a variant's summer and winter specs for
    the same axle silently overwrote each other on upsert. Since KAN-75 the
    key holds the raw AchsenCode and PneuTyp."""

    tenant_id = uuid.uuid4()
    variant = _bare_variant(db_session)
    db_session.add(_tyre(tenant_id, variant, "2", "121", "X"))
    db_session.flush()
    db_session.add(_tyre(tenant_id, variant, "2", "122", "Y"))
    db_session.flush()  # no IntegrityError — season is part of the key now

    rows = db_session.query(TyreSpecCache).filter_by(tenant_id=tenant_id, model_variant_id=variant.id).all()
    assert {(r.season_code, r.size) for r in rows} == {("121", "X"), ("122", "Y")}


def test_tyre_spec_cache_still_rejects_a_true_duplicate(db_session):
    """KAN-596 (ADR-141): the key is one approved wheel — axle, season,
    dimension and rim. The same wheel twice is refused; a second dimension
    for the same axle and season is another wheel (test below)."""

    tenant_id = uuid.uuid4()
    variant = _bare_variant(db_session)
    first = _tyre(tenant_id, variant, "2", "121", "225/45 R18")
    first.rims = "8Jx18"
    db_session.add(first)
    db_session.flush()
    again = _tyre(tenant_id, variant, "2", "121", "225/45 R18")
    again.rims = "8Jx18"
    db_session.add(again)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_tyre_spec_cache_keeps_a_second_dimension_of_the_same_axle_and_season(db_session):
    """KAN-596: a Typenschein approves several dimensions per axle and season;
    before, the key kept the first only."""

    tenant_id = uuid.uuid4()
    variant = _bare_variant(db_session)
    db_session.add(_tyre(tenant_id, variant, "2", "121", "205/60 R16"))
    db_session.add(_tyre(tenant_id, variant, "2", "121", "225/45 R18"))
    db_session.flush()
    assert db_session.query(TyreSpecCache).filter_by(tenant_id=tenant_id, model_variant_id=variant.id).count() == 2


@NEEDS_ITS_OWN_CONNECTION
def test_a_lookup_links_each_variant_to_its_typenscheine_once(db_session):
    """KAN-42 (C-D): FR-C-02 step 4 resolves a Typenschein through
    `vehicle_variant_type_approval`, which the sync never wrote before."""

    from app.vehicle.models.catalogue import TypeApproval, VariantTypeApproval
    from app.vehicle.services.catalogue import find_model_variants_by_type_approval

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    look_up_demo_variants(db_session, tenant_id)
    look_up_demo_variants(db_session, tenant_id)

    assert [v.name for v in find_model_variants_by_type_approval(db_session, "2CD456")] == ["Golf GTI 2.0 TSI DSG"]
    assert db_session.query(TypeApproval).filter_by(type_approval_number="2CD456").count() == 1
    assert db_session.query(VariantTypeApproval).count() == 3


@NEEDS_ITS_OWN_CONNECTION
def test_a_variant_mirrored_without_its_typenscheine_is_linked_by_its_next_lookup(db_session):
    """KAN-190 item 3: a variant mirrored before KAN-42 has no
    `vehicle_variant_type_approval` rows. No backfill fetches them — no
    provider is ever asked for its whole catalogue (ADR-023 as amended,
    KAN-485) — and none is needed: every single lookup of an existing
    variant links its Typenscheine (`upsert_model_variant`), so the next one
    repairs it. Here the links are removed after a first lookup, as such a
    variant has none."""

    from app.vehicle.models.catalogue import VariantTypeApproval
    from app.vehicle.services.catalogue import find_model_variants_by_type_approval

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)
    look_up_demo_variants(db_session, tenant_id)
    db_session.query(VariantTypeApproval).delete()
    db_session.commit()
    assert find_model_variants_by_type_approval(db_session, "2CD456") == []

    look_up_demo_variants(db_session, tenant_id)

    assert [v.name for v in find_model_variants_by_type_approval(db_session, "2CD456")] == ["Golf GTI 2.0 TSI DSG"]
    assert db_session.query(VariantTypeApproval).count() == 3


# --- KAN-83: a resolved gap heals the variants it left NULL -------------------


def _seed_reference_list(db_session, list_code: str, values: tuple[str, ...]) -> None:
    ref_list = ReferenceList(list_code=list_code)
    db_session.add(ref_list)
    db_session.flush()
    for code in values:
        db_session.add(
            ReferenceValue(
                list_id=ref_list.id,
                value_code=code,
                label_de=code,
                label_fr=code,
                label_it=code,
                label_en=code,
            )
        )
    db_session.commit()


def _variant(db_session, fz_key: str) -> ModelVariant:
    variant = catalogue_sync.find_variant_by_fz_key(db_session, provider_code="auto_i_dat_mock", fz_key=fz_key)
    assert variant is not None
    return variant


def _looked_up_tenant(db_session) -> uuid.UUID:
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)
    look_up_demo_variants(db_session, tenant_id)
    return tenant_id


def _fuel_gap(db_session, provider_code: str) -> MappingGap:
    gap = db_session.scalar(
        select(MappingGap).where(
            MappingGap.provider == "auto_i_dat_mock",
            MappingGap.code_group == "fuel_type",
            MappingGap.provider_code == provider_code,
        )
    )
    assert gap is not None
    return gap


@NEEDS_ITS_OWN_CONNECTION
def test_the_sync_records_each_variants_raw_provider_codes(db_session):
    _looked_up_tenant(db_session)

    rows = db_session.scalars(
        select(VariantProviderCode).where(VariantProviderCode.model_variant_id == _variant(db_session, "FZ100001").id)
    ).all()

    assert {(r.provider, r.vehicle_kind, r.code_group, r.provider_code) for r in rows} == {
        ("auto_i_dat_mock", "01", "vehicle_kind", "01"),
        ("auto_i_dat_mock", "01", "fuel_type", "3"),
        ("auto_i_dat_mock", "01", "body_style", "5"),
        ("auto_i_dat_mock", "01", "drivetrain", "1"),
        ("auto_i_dat_mock", "01", "transmission", "1"),
        ("auto_i_dat_mock", "01", "consumption_norm", "4"),  # KAN-547: the spec block's coded fields too
    }


@NEEDS_ITS_OWN_CONNECTION
def test_resolving_a_gap_fills_the_variants_it_left_null_at_once(db_session):
    """No sync runs after the resolve: the variants are filled by the
    resolve itself, and only those whose raw code is the gap's."""

    _looked_up_tenant(db_session)
    _seed_reference_list(db_session, "fuel_type", ("petrol", "diesel"))
    assert _variant(db_session, "FZ100001").fuel_type is None
    gap = _fuel_gap(db_session, "3")
    actor_id = uuid.uuid4()

    catalogue_admin.resolve_gap(
        db_session,
        gap=gap,
        canonical_list_code="fuel_type",
        canonical_value_code="petrol",
        actor_id=actor_id,
    )

    assert _variant(db_session, "FZ100001").fuel_type == "petrol"
    assert _variant(db_session, "FZ100002").fuel_type == "petrol"
    assert _variant(db_session, "FZ100003").fuel_type is None  # raw code "4" — a different gap
    events = list_audit_events(db_session, entity_type="vehicle_mapping_gap", entity_id=gap.id, tenant_id=None)
    assert events[0].after["variantsFilled"] == 2


@NEEDS_ITS_OWN_CONNECTION
def test_resolving_a_gap_never_overwrites_a_field_that_already_holds_a_value(db_session):
    _looked_up_tenant(db_session)
    _seed_reference_list(db_session, "fuel_type", ("petrol", "diesel"))
    kept = _variant(db_session, "FZ100001")
    kept.fuel_type = "diesel"
    version = kept.version
    db_session.commit()

    catalogue_admin.resolve_gap(
        db_session,
        gap=_fuel_gap(db_session, "3"),
        canonical_list_code="fuel_type",
        canonical_value_code="petrol",
        actor_id=uuid.uuid4(),
    )

    kept = _variant(db_session, "FZ100001")
    assert kept.fuel_type == "diesel"
    assert kept.version == version
    filled = _variant(db_session, "FZ100002")
    assert filled.fuel_type == "petrol"
    assert filled.version == 2


@NEEDS_ITS_OWN_CONNECTION
def test_a_resync_fills_a_coded_field_whose_code_has_been_mapped_since(db_session):
    """A mapping written outside a resolve (a seed migration) is picked up
    by the next visit of the variant."""

    tenant_id = _looked_up_tenant(db_session)
    db_session.add(
        ProviderCodeMap(
            provider="auto_i_dat_mock",
            vehicle_kind="01",
            code_group="body_style",
            provider_code="5",
            canonical_list_code="body_style",
            canonical_value_code="hatchback",
        )
    )
    db_session.commit()

    look_up_demo_variants(db_session, tenant_id)

    assert _variant(db_session, "FZ100001").body_style == "hatchback"
    assert _variant(db_session, "FZ100003").body_style is None  # raw code "4", still unmapped


@NEEDS_ITS_OWN_CONNECTION
def test_a_resync_fills_null_master_fields_and_never_overwrites_set_ones(db_session):
    tenant_id = _looked_up_tenant(db_session)
    emptied = _variant(db_session, "FZ100001")
    emptied.model_year_to = None
    emptied.base_price = None
    emptied.werkscode = None
    edited = _variant(db_session, "FZ100002")
    edited.base_price = Decimal("1.00")
    edited.werkscode = "LOCAL"
    edited.name = "Locally renamed"
    db_session.commit()

    look_up_demo_variants(db_session, tenant_id)

    emptied = _variant(db_session, "FZ100001")
    assert (emptied.model_year_to, emptied.base_price, emptied.werkscode) == (2021, Decimal("28900.00"), "ALF14TB")
    assert emptied.version == 2  # one bump for the one visit that filled
    edited = _variant(db_session, "FZ100002")
    assert (edited.base_price, edited.werkscode, edited.name) == (Decimal("1.00"), "LOCAL", "Locally renamed")


@NEEDS_ITS_OWN_CONNECTION
def test_a_resync_of_an_unchanged_variant_does_not_bump_its_version(db_session):
    tenant_id = _looked_up_tenant(db_session)
    before = _variant(db_session, "FZ100001").version

    look_up_demo_variants(db_session, tenant_id)

    assert _variant(db_session, "FZ100001").version == before


@NEEDS_ITS_OWN_CONNECTION
def test_a_raw_code_the_provider_changes_is_recorded_and_a_dropped_one_removed(db_session, monkeypatch):
    tenant_id = _looked_up_tenant(db_session)
    original = MockAutoIDatAdapter.fetch_variant

    def _changed(self, fz_key):
        master = original(self, fz_key)
        if fz_key != "FZ100001":
            return master
        return dataclasses.replace(master, fuel_type_code="9", drivetrain_code=None)

    monkeypatch.setattr(MockAutoIDatAdapter, "fetch_variant", _changed)
    look_up_demo_variants(db_session, tenant_id)

    codes = {
        r.code_group: r.provider_code
        for r in db_session.scalars(
            select(VariantProviderCode).where(
                VariantProviderCode.model_variant_id == _variant(db_session, "FZ100001").id
            )
        )
    }
    assert codes["fuel_type"] == "9"
    assert "drivetrain" not in codes


def _variant_with_raw_fuel_code(db_session, *, provider: str, vehicle_kind: str, raw_code: str) -> ModelVariant:
    variant = _bare_variant(db_session, name=f"{provider} kind {vehicle_kind}")
    db_session.add(
        VariantProviderCode(
            model_variant_id=variant.id,
            provider=provider,
            vehicle_kind=vehicle_kind,
            code_group="fuel_type",
            provider_code=raw_code,
        )
    )
    db_session.commit()
    return variant


@NEEDS_ITS_OWN_CONNECTION
def test_resolving_a_gap_fills_only_variants_of_its_own_vehicle_kind_and_provider(db_session):
    """The vehicle-kind qualifier is load-bearing (`ProviderCodeMap`): fuel
    code "3" is Diesel for a car and Bleifrei for a motorcycle. A gap for
    one kind, or one provider, never fills another's variants."""

    _looked_up_tenant(db_session)
    _seed_reference_list(db_session, "fuel_type", ("petrol", "diesel"))
    motorcycle = _variant_with_raw_fuel_code(db_session, provider="auto_i_dat_mock", vehicle_kind="2", raw_code="3")
    other_provider = _variant_with_raw_fuel_code(db_session, provider="auto_i_dat", vehicle_kind="1", raw_code="3")

    catalogue_admin.resolve_gap(
        db_session,
        gap=_fuel_gap(db_session, "3"),
        canonical_list_code="fuel_type",
        canonical_value_code="petrol",
        actor_id=uuid.uuid4(),
    )

    assert _variant(db_session, "FZ100001").fuel_type == "petrol"
    db_session.expire_all()
    assert db_session.get(ModelVariant, motorcycle.id).fuel_type is None
    assert db_session.get(ModelVariant, other_provider.id).fuel_type is None


@NEEDS_ITS_OWN_CONNECTION
def test_resolving_a_gap_whose_code_group_is_not_a_variant_field_fills_nothing(db_session):
    """An equipment feature is an option attribute, not a variant column:
    its resolve succeeds and fills no variant (never an AttributeError).
    No option's SuchCode list carries "HS", so no option gains it either
    (the option heal is KAN-250's tests below)."""

    _looked_up_tenant(db_session)
    _seed_reference_list(db_session, "equipment_feature", ("heated_seats",))
    resolve_provider_code(
        db_session,
        provider="auto_i_dat_mock",
        vehicle_kind="01",
        code_group="equipment_feature",
        provider_code="HS",
    )
    # The lookups record equipment-feature gaps of their own; this one is "HS".
    gap = _feature_gap(db_session, "HS")

    catalogue_admin.resolve_gap(
        db_session,
        gap=gap,
        canonical_list_code="equipment_feature",
        canonical_value_code="heated_seats",
        actor_id=uuid.uuid4(),
    )

    assert gap.resolved is True
    events = list_audit_events(db_session, entity_type="vehicle_mapping_gap", entity_id=gap.id, tenant_id=None)
    assert events[0].after["variantsFilled"] == 0
    assert events[0].after["optionsFilled"] == 0


# --- KAN-250: a resolved equipment_feature gap reaches the options it blocked ---------------


def _option(db_session, tenant_id: uuid.UUID, fz_key: str, option_code: str) -> VariantOption:
    return db_session.scalars(
        select(VariantOption).where(
            VariantOption.tenant_id == tenant_id,
            VariantOption.model_variant_id == _variant(db_session, fz_key).id,
            VariantOption.option_code == option_code,
        )
    ).one()


def _features(db_session, option: VariantOption) -> set[tuple[uuid.UUID, str]]:
    db_session.expire_all()
    return {
        (row.tenant_id, row.feature_value_code)
        for row in db_session.scalars(
            select(VariantOptionEquipmentFeature).where(VariantOptionEquipmentFeature.variant_option_id == option.id)
        )
    }


def _feature_gap(db_session, provider_code: str) -> MappingGap:
    return db_session.scalars(
        select(MappingGap).where(
            MappingGap.code_group == "equipment_feature", MappingGap.provider_code == provider_code
        )
    ).one()


@NEEDS_ITS_OWN_CONNECTION
def test_the_sync_records_each_options_raw_such_codes_in_its_tenant(db_session):
    tenant_id = _looked_up_tenant(db_session)

    rows = db_session.scalars(
        select(VariantOptionProviderCode).where(
            VariantOptionProviderCode.variant_option_id == _option(db_session, tenant_id, "FZ100002", "NAV").id
        )
    ).all()

    # Resolved or not, every raw code is kept — none is mapped in this test.
    assert {(r.tenant_id, r.provider, r.vehicle_kind, r.code_group, r.provider_code) for r in rows} == {
        (tenant_id, "auto_i_dat_mock", "01", "equipment_feature", "navigation"),
        (tenant_id, "auto_i_dat_mock", "01", "equipment_feature", "APPLE_CARPLAY"),
    }


@NEEDS_ITS_OWN_CONNECTION
def test_resolving_an_equipment_feature_gap_adds_the_feature_to_every_tenants_options_at_once(db_session):
    """No sync runs after the resolve: each tenant's option whose SuchCode
    list carried the code gains the feature in that tenant's partition."""

    tenant_a = _looked_up_tenant(db_session)
    tenant_b = uuid.uuid4()
    _make_connection(db_session, db_session.scalars(select(IntegrationProvider)).one(), tenant_id=tenant_b)
    look_up_demo_variants(db_session, tenant_b)
    _seed_reference_list(db_session, "equipment_feature", ("apple_carplay", "navigation"))
    nav_a, nav_b = _option(db_session, tenant_a, "FZ100002", "NAV"), _option(db_session, tenant_b, "FZ100002", "NAV")
    led_a = _option(db_session, tenant_a, "FZ100002", "LED")
    assert _features(db_session, nav_a) == set()
    gap = _feature_gap(db_session, "APPLE_CARPLAY")

    catalogue_admin.resolve_gap(
        db_session,
        gap=gap,
        canonical_list_code="equipment_feature",
        canonical_value_code="apple_carplay",
        actor_id=uuid.uuid4(),
    )

    assert _features(db_session, nav_a) == {(tenant_a, "apple_carplay")}
    assert _features(db_session, nav_b) == {(tenant_b, "apple_carplay")}
    assert _features(db_session, led_a) == set()  # its list never carried the code
    events = list_audit_events(db_session, entity_type="vehicle_mapping_gap", entity_id=gap.id, tenant_id=None)
    assert events[0].after["optionsFilled"] == 2


@NEEDS_ITS_OWN_CONNECTION
def test_resolving_a_feature_an_option_already_carries_adds_no_second_row(db_session):
    tenant_id = _looked_up_tenant(db_session)
    _seed_reference_list(db_session, "equipment_feature", ("navigation",))
    nav = _option(db_session, tenant_id, "FZ100002", "NAV")
    db_session.add(
        VariantOptionEquipmentFeature(tenant_id=tenant_id, variant_option_id=nav.id, feature_value_code="navigation")
    )
    db_session.commit()

    catalogue_admin.resolve_gap(
        db_session,
        gap=_feature_gap(db_session, "navigation"),
        canonical_list_code="equipment_feature",
        canonical_value_code="navigation",
        actor_id=uuid.uuid4(),
    )

    assert _features(db_session, nav) == {(tenant_id, "navigation")}


@NEEDS_ITS_OWN_CONNECTION
def test_resolving_a_feature_gap_reaches_only_options_of_its_own_vehicle_kind_and_provider(db_session):
    tenant_id = _looked_up_tenant(db_session)
    _seed_reference_list(db_session, "equipment_feature", ("apple_carplay",))
    variant = _bare_variant(db_session)
    others = []
    for provider, kind in (("auto_i_dat_mock", "2"), ("auto_i_dat", "1")):
        option = VariantOption(
            tenant_id=tenant_id, model_variant_id=variant.id, option_code=f"X{provider}{kind}", description="x"
        )
        db_session.add(option)
        db_session.flush()
        db_session.add(
            VariantOptionProviderCode(
                tenant_id=tenant_id,
                variant_option_id=option.id,
                provider=provider,
                vehicle_kind=kind,
                code_group="equipment_feature",
                provider_code="APPLE_CARPLAY",
            )
        )
        others.append(option)
    db_session.commit()

    catalogue_admin.resolve_gap(
        db_session,
        gap=_feature_gap(db_session, "APPLE_CARPLAY"),
        canonical_list_code="equipment_feature",
        canonical_value_code="apple_carplay",
        actor_id=uuid.uuid4(),
    )

    assert _features(db_session, _option(db_session, tenant_id, "FZ100002", "NAV")) == {(tenant_id, "apple_carplay")}
    assert all(_features(db_session, option) == set() for option in others)


def test_a_such_code_the_provider_drops_is_removed_from_the_options_raw_codes(db_session):
    tenant_id = uuid.uuid4()
    variant = _bare_variant(db_session)
    option = VariantOption(tenant_id=tenant_id, model_variant_id=variant.id, option_code="NAV", description="Nav")
    db_session.add(option)
    db_session.commit()

    for codes in (["navigation", "APPLE_CARPLAY"], ["APPLE_CARPLAY"]):
        catalogue_sync.sync_option_equipment_features(
            db_session,
            tenant_id=tenant_id,
            provider_code="auto_i_dat_mock",
            vehicle_kind_code="1",
            variant_option=option,
            feature_codes=codes,
        )
        db_session.commit()

    assert {
        r.provider_code
        for r in db_session.scalars(
            select(VariantOptionProviderCode).where(VariantOptionProviderCode.variant_option_id == option.id)
        )
    } == {"APPLE_CARPLAY"}
