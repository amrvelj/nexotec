"""KAN-769 (ADR-190): the vehicle context's word lists sort under the collation — case ignored,
Ä/é beside A/e — not in the test database's byte order (`C.UTF-8`) nor by Python's `.lower()`
(«ärztin» after «z»). Each expected order is the database's own
(`tests.text_sort_support.text_sort_order`)."""

import uuid

import pytest

from app.integration.services import resilience
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant, TypeApproval, VariantTypeApproval
from app.vehicle.schemas.catalogue import CatalogueBrowseState
from app.vehicle.services import catalogue_browse
from app.vehicle.services import identification as identification_service
from app.vehicle.services.catalogue import find_model_variants_by_type_approval
from app.vehicle.services.identification_types import IdentificationOutcome
from tests import identapi_fixtures as fix
from tests import identapi_support as fx
from tests.identapi_support import FakeAutoi
from tests.postgres_only import POSTGRES_SQL
from tests.text_sort_support import text_sort_order
from tests.vehicle.test_provider_code_map_seed import _seed_migration

NAMES = ["Zeta", "alpha", "Ärztin", "Arzt", "école"]
BRANDS_PATH = "/vehiclecatalog/brand/passenger"


@pytest.fixture(autouse=True)
def _fresh_adapter_state():
    yield
    fx.uninstall()
    for connection_id in list(resilience._CIRCUITS):
        resilience.reset_circuit(connection_id)


def _collated(db_session, values: list[str]) -> list[str]:
    """The collated order, checked to differ from byte order — else the test proves nothing."""

    expected = text_sort_order(db_session, values)
    assert expected != sorted(values), "byte order and collated order must differ"
    return expected


def _variants(db, names: list[str], **fields) -> list[ModelVariant]:
    brand = Brand(code=f"b-{uuid.uuid4().hex[:6]}", display_name="Testmarke")
    db.add(brand)
    db.flush()
    group = ModelGroup(brand_id=brand.id, name="Gruppe")
    db.add(group)
    db.flush()
    variants = [ModelVariant(model_group_id=group.id, name=name, model_year_from=2020, **fields) for name in names]
    db.add_all(variants)
    db.flush()
    return variants


@POSTGRES_SQL
def test_kan769_variants_by_type_approval_sort_by_name_under_the_collation_each_once(db_session):
    """Two `TypeApproval` rows carry the same number and both link every variant: each is still
    returned once (the semi-join that replaced the DISTINCT)."""

    variants = _variants(db_session, NAMES)
    for _ in range(2):
        db_session.add(
            TypeApproval(
                type_approval_number="7KA769",
                variant_links=[VariantTypeApproval(model_variant_id=variant.id) for variant in variants],
            )
        )
    db_session.commit()

    found = find_model_variants_by_type_approval(db_session, "7KA769")

    assert [variant.name for variant in found] == _collated(db_session, NAMES)
    assert len({variant.id for variant in found}) == len(found)


@POSTGRES_SQL
def test_kan769_stored_variants_of_a_werkscode_sort_by_name_under_the_collation(db_session):
    """No connection: the Werkscode answer is the stored variants alone, in their stored order."""

    _variants(db_session, NAMES, werkscode="WK769X")
    db_session.commit()

    result = identification_service.identify(db_session, tenant_id=uuid.uuid4(), actor_id=None, query="WK769X")

    assert result.outcome == IdentificationOutcome.VARIANTS
    assert [candidate.variant_name for candidate in result.variants] == _collated(db_session, NAMES)


@POSTGRES_SQL
def test_kan769_provider_brands_sort_by_name_under_the_collation(db_session, monkeypatch):
    brands = [{"brand": name, "brandNr": str(900 + i), "models": [], "modelssearch": []} for i, name in enumerate(NAMES)]
    s = fx.connected(db_session, monkeypatch, fake=FakeAutoi(api_bodies={BRANDS_PATH: fix.body(brands)}))
    _seed_migration()._seed(db_session.connection())
    db_session.commit()

    result = catalogue_browse.list_brands(
        db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind="passenger_car"
    )

    assert result.state is CatalogueBrowseState.OK
    names = [brand.name for brand in result.items]
    assert names == _collated(db_session, NAMES)
    assert names != sorted(NAMES, key=str.lower), "the old `.lower()` key must disagree too"


@POSTGRES_SQL
def test_kan769_a_variants_energy_labels_sort_by_gearbox_under_the_collation(db_session):
    """`energy_labels_for_configuration`: the gearbox is the provider's word as delivered
    («Automat», «Manuell»), shown to the seller — a word column, not a code."""

    from app.vehicle.models.energy_rating import ModelVariantEnergyLabel
    from app.vehicle.services.energy_rating import energy_labels_for_configuration

    (variant,) = _variants(db_session, ["Golf 1.5 TSI"])
    db_session.add_all(
        ModelVariantEnergyLabel(
            model_variant_id=variant.id, label_year=2024, gearbox=gearbox, energy_efficiency_category="B"
        )
        for gearbox in NAMES
    )
    db_session.commit()

    labels = energy_labels_for_configuration(
        db_session, model_variant_id=variant.id, first_registration_year=2024, model_year=None
    )

    assert [label.gearbox for label in labels] == _collated(db_session, NAMES)
