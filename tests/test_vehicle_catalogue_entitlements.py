"""WP-6 PR-5: entitlement-based degradation over the catalogue mirror —
per-capability, never wholesale, and a dealer with no provider contract
at all keeps a fully usable module.
"""

import uuid

from app.core.auth import create_access_token
from app.core.base import utcnow
from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.entitlement import EntitlementSource, IntegrationEntitlement
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.vehicle.models.catalogue import ModelVariant, VariantOption, VariantOptionRelation
from app.vehicle.services import catalogue_entitlements, catalogue_sync

VALID_VIN = "1HGCM82633A004352"


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


def _make_connection(db_session, provider, *, tenant_id):
    return connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )


def _deny(db_session, connection_id, capability_code):
    db_session.add(
        IntegrationEntitlement(
            connection_id=connection_id, capability_code=capability_code, granted=False,
            source=EntitlementSource.DECLARED, checked_at=utcnow(),
        )
    )
    db_session.commit()


# --- service level ---------------------------------------------------------


def test_no_connection_degrades_every_capability_but_stays_a_valid_response(db_session):
    entitlements = catalogue_entitlements.get_catalogue_entitlements(db_session, tenant_id=uuid.uuid4())
    assert entitlements.has_connection is False
    assert entitlements.images is False
    assert entitlements.packages is False


def test_unprobed_capability_defaults_to_granted(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    entitlements = catalogue_entitlements.get_catalogue_entitlements(db_session, tenant_id=tenant_id)
    assert entitlements.has_connection is True
    assert entitlements.images is True
    assert entitlements.packages is True


def test_a_declared_denial_is_respected(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    connection = _make_connection(db_session, provider, tenant_id=tenant_id)
    _deny(db_session, connection.id, "packages")

    entitlements = catalogue_entitlements.get_catalogue_entitlements(db_session, tenant_id=tenant_id)
    assert entitlements.packages is False
    assert entitlements.images is True  # degradation is per capability, never wholesale


def test_specification_flattens_option_groups_without_packages_entitlement(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    connection = _make_connection(db_session, provider, tenant_id=tenant_id)
    _deny(db_session, connection.id, "packages")

    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)
    master = MockAutoIDatAdapter().fetch_vehicle_master_data("FZ100002")
    variant = catalogue_sync.upsert_model_variant(db_session, provider_code="auto_i_dat_mock", master=master)

    spec = catalogue_entitlements.get_catalogue_specification(db_session, tenant_id=tenant_id, model_variant_id=variant.id)
    assert spec.packages_available is False
    assert len(spec.options) >= 1
    assert all(o.option_group is None for o in spec.options)


def test_specification_hides_images_without_images_entitlement(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    connection = _make_connection(db_session, provider, tenant_id=tenant_id)
    _deny(db_session, connection.id, "images")

    result = catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)
    variant = db_session.query(ModelVariant).first()

    spec = catalogue_entitlements.get_catalogue_specification(db_session, tenant_id=tenant_id, model_variant_id=variant.id)
    assert spec.images_available is False
    assert spec.images == []
    assert spec.dealer_can_upload_images is True
    assert result.variants_synced == 3


def test_specification_carries_the_new_c_e_fields_on_options_colours_and_tyres(db_session):
    """KAN-43 (C-E) — is_included/is_package/equipment_features on
    options, price on colours, season/remark on tyres."""

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)
    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)

    golf = db_session.query(ModelVariant).filter_by(name="Golf GTI 2.0 TSI DSG").one()
    spec = catalogue_entitlements.get_catalogue_specification(db_session, tenant_id=tenant_id, model_variant_id=golf.id)

    by_code = {o.option_code: o for o in spec.options}
    assert by_code["AC"].is_included is True
    assert by_code["WNTR"].is_package is True

    colours_by_code = {c.colour_code: c for c in spec.colours}
    assert colours_by_code["RED"].price is not None

    seasons = {t.season for t in spec.tyre_specs}
    assert "summer" in seasons and "winter" in seasons
    assert any(t.remark is not None for t in spec.tyre_specs)


def test_specification_omits_option_relations_without_packages_entitlement(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    connection = _make_connection(db_session, provider, tenant_id=tenant_id)
    _deny(db_session, connection.id, "packages")

    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)
    golf = db_session.query(ModelVariant).filter_by(name="Golf GTI 2.0 TSI DSG").one()

    spec = catalogue_entitlements.get_catalogue_specification(db_session, tenant_id=tenant_id, model_variant_id=golf.id)
    assert spec.packages_available is False
    assert spec.option_relations == []


def test_specification_renders_a_relation_with_the_related_option_denormalised(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)
    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)

    golf = db_session.query(ModelVariant).filter_by(name="Golf GTI 2.0 TSI DSG").one()
    options = {
        o.option_code: o
        for o in db_session.query(VariantOption).filter_by(tenant_id=tenant_id, model_variant_id=golf.id).all()
    }
    db_session.add(
        VariantOptionRelation(
            tenant_id=tenant_id, model_variant_id=golf.id, model_year=golf.model_year_from,
            from_option_id=options["WNTR"].id, to_option_id=options["LED"].id, relation_type="excludes",
        )
    )
    db_session.commit()

    spec = catalogue_entitlements.get_catalogue_specification(db_session, tenant_id=tenant_id, model_variant_id=golf.id)
    assert len(spec.option_relations) == 1
    relation = spec.option_relations[0]
    assert relation.from_option_id == options["WNTR"].id
    assert relation.to_option_id == options["LED"].id
    assert relation.to_option_code == "LED"
    assert relation.to_option_description == "LED headlights"
    assert relation.relation_type == "excludes"


def test_specification_with_no_catalogue_match_is_empty_but_valid(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    spec = catalogue_entitlements.get_catalogue_specification(db_session, tenant_id=tenant_id, model_variant_id=None)
    assert spec.has_catalogue_match is False
    assert spec.options == []
    assert spec.has_provider_connection is True


# --- API level -------------------------------------------------------------


def _token(tenant_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset(), is_dealer_manager=True,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_catalogue_specification_endpoint_for_an_unmatched_vehicle(client, db_session):
    tenant_id = uuid.uuid4()
    token = _token(tenant_id)
    created = client.post("/v1/vehicle-mdm", json={"vin": VALID_VIN}, headers=_bearer(token)).json()["vehicle"]

    response = client.get(f"/v1/vehicle-mdm/{created['id']}/catalogue-specification", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hasCatalogueMatch"] is False
    assert body["hasProviderConnection"] is False
    assert body["dealerCanUploadImages"] is True
    assert body["options"] == []


def test_catalogue_specification_endpoint_for_a_matched_vehicle_with_full_entitlements(client, db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)
    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)
    variant = db_session.query(ModelVariant).first()

    token = _token(tenant_id)
    created = client.post(
        "/v1/vehicle-mdm", json={"vin": VALID_VIN, "catalogueVariantId": str(variant.id)}, headers=_bearer(token)
    ).json()["vehicle"]

    response = client.get(f"/v1/vehicle-mdm/{created['id']}/catalogue-specification", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hasCatalogueMatch"] is True
    assert body["hasProviderConnection"] is True
    assert body["imagesAvailable"] is True


# --- the Configurator's own read, keyed by model_variant_id (KAN-43) -----


def test_variant_specification_endpoint_for_the_configurator(client, db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)
    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)
    variant = db_session.query(ModelVariant).filter_by(name="Golf GTI 2.0 TSI DSG").one()

    token = _token(tenant_id)
    response = client.get(f"/v1/catalogue/variants/{variant.id}/specification", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hasCatalogueMatch"] is True
    by_code = {o["optionCode"]: o for o in body["options"]}
    assert by_code["AC"]["isIncluded"] is True
    assert by_code["WNTR"]["isPackage"] is True


def test_variant_specification_endpoint_404s_for_an_unknown_variant(client, db_session):
    token = _token(uuid.uuid4())
    response = client.get(f"/v1/catalogue/variants/{uuid.uuid4()}/specification", headers=_bearer(token))
    assert response.status_code == 404


def test_variant_specification_endpoint_needs_no_manager_flag(client, db_session):
    """Same posture as catalogue browse — any authenticated user reads it."""

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)
    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)
    variant = db_session.query(ModelVariant).first()

    non_manager_token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset(), is_dealer_manager=False,
    )
    response = client.get(f"/v1/catalogue/variants/{variant.id}/specification", headers=_bearer(non_manager_token))
    assert response.status_code == 200, response.text


# --- the generic capability-check endpoint (Sales/Valuation banners) -----


def test_capability_check_endpoint_defaults_to_granted_when_connected(client, db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)
    token = _token(tenant_id)

    response = client.get("/v1/integrations/capabilities/valuation", headers=_bearer(token))
    assert response.status_code == 200, response.text
    assert response.json() == {"capabilityCode": "valuation", "granted": True}


def test_capability_check_endpoint_returns_false_with_no_connection(client, db_session):
    token = _token(uuid.uuid4())

    response = client.get("/v1/integrations/capabilities/valuation", headers=_bearer(token))
    assert response.status_code == 200, response.text
    assert response.json() == {"capabilityCode": "valuation", "granted": False}


def test_capability_check_endpoint_respects_a_declared_denial(client, db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    connection = _make_connection(db_session, provider, tenant_id=tenant_id)
    _deny(db_session, connection.id, "packages")
    token = _token(tenant_id)

    response = client.get("/v1/integrations/capabilities/packages", headers=_bearer(token))
    assert response.json()["granted"] is False

    response = client.get("/v1/integrations/capabilities/valuation", headers=_bearer(token))
    assert response.json()["granted"] is True


def test_capability_check_endpoint_needs_no_manager_flag(client, db_session):
    """Unlike `integration_connections`, this reveals nothing sensitive —
    any authenticated user in the tenant may ask it."""

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)
    non_manager_token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset(), is_dealer_manager=False,
    )

    response = client.get("/v1/integrations/capabilities/valuation", headers=_bearer(non_manager_token))
    assert response.status_code == 200, response.text
