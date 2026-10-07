"""Configurator C-D (KAN-42) — the FR-C-02 identification waterfall, the
plate-lookup cache and best-match confirmation.

Runs against the mock auto-i-dat adapter: no staging account exists, so
nothing here says anything about the real provider's behaviour.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.auth import AccessRole, create_access_token
from app.core.base import utcnow
from app.core.errors import UnprocessableEntityError
from app.core.outbox_model import OutboxMessage
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.connection import ConnectionEnvironment, IntegrationConnection
from app.integration.models.entitlement import EntitlementSource, IntegrationEntitlement
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant, TypeApproval, VariantTypeApproval
from app.vehicle.models.configuration import (
    ConfigurationMatchMethod,
    ConfigurationMatchStatus,
    ConfigurationMode,
    ConfigurationSource,
    VehicleConfiguration,
)
from app.vehicle.models.plate_lookup_cache import PlateLookupCacheEntry
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.schemas.configuration import ConfigurationCreate
from app.vehicle.services import catalogue_sync, plate_lookup_cache
from app.vehicle.services import configuration as configuration_service
from app.vehicle.services import identification as svc
from app.vehicle.services.identification import (
    IdentificationNote,
    IdentificationOutcome,
    IdentifierKind,
    NewPriceSource,
)

# --- fixtures -----------------------------------------------------------


def _mock_provider(db) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code="auto_i_dat_mock",
        category="vehicle_data",
        display_name="auto-i-dat (mock)",
        auth_type="none",
        required_secret_slots=[],
        capability_codes=["fahrzeuge", "optionen", "kontrollschild", "pneu", "bewertung"],
    )
    db.add(provider)
    db.commit()
    return provider


def _connected_tenant(db, *, seed: bool = True) -> uuid.UUID:
    provider = _mock_provider(db)
    tenant_id = uuid.uuid4()
    connection_service.create_connection(
        db,
        tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    if seed:
        catalogue_sync.seed_tenant_catalogue(db, tenant_id=tenant_id)
    return tenant_id


def _plate_calls(db, tenant_id) -> int:
    return db.scalar(
        select(func.count()).select_from(IntegrationCallLog).where(
            IntegrationCallLog.tenant_id == tenant_id, IntegrationCallLog.capability == "kontrollschild"
        )
    )


def _all_calls(db) -> int:
    return db.scalar(select(func.count()).select_from(IntegrationCallLog))


def _variant_sharing_type_approval(db, type_approval_number: str, name: str) -> ModelVariant:
    brand = Brand(code=f"b-{uuid.uuid4().hex[:6]}", display_name="Volkswagen")
    db.add(brand)
    db.flush()
    group = ModelGroup(brand_id=brand.id, name="Golf")
    db.add(group)
    db.flush()
    variant = ModelVariant(model_group_id=group.id, name=name, model_year_from=2021)
    db.add(variant)
    db.flush()
    approval = db.scalar(select(TypeApproval).where(TypeApproval.type_approval_number == type_approval_number))
    if approval is None:
        approval = TypeApproval(type_approval_number=type_approval_number)
        db.add(approval)
        db.flush()
    db.add(VariantTypeApproval(model_variant_id=variant.id, type_approval_id=approval.id))
    db.commit()
    return variant


def _mdm(db, *, vin: str, stammnummer: str | None = None) -> VehicleMdm:
    vehicle = VehicleMdm(vin=vin, vehicle_number=f"F-{uuid.uuid4().int % 1_000_000:06d}", stammnummer=stammnummer)
    db.add(vehicle)
    db.commit()
    return vehicle


def _bearer(tenant_id: uuid.UUID, role: AccessRole = AccessRole.SALES) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(),
        roles=frozenset({role}), is_dealer_manager=False,
    )
    return {"Authorization": f"Bearer {token}"}


# --- one input ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "kind", "value"),
    [
        ("WVWZZZ1KZAW000001", IdentifierKind.VIN, "WVWZZZ1KZAW000001"),
        ("wvwzzz1kzaw000001", IdentifierKind.VIN, "WVWZZZ1KZAW000001"),
        ("ZH 123 456", IdentifierKind.KONTROLLSCHILD, "ZH123456"),
        ("zh123456", IdentifierKind.KONTROLLSCHILD, "ZH123456"),
        ("626.702.193", IdentifierKind.STAMMNUMMER, "626702193"),
        ("626702193", IdentifierKind.STAMMNUMMER, "626702193"),
        ("1SC653", IdentifierKind.TYPENSCHEIN, "1SC653"),
        ("ALF14TB", IdentifierKind.WERKSCODE, "ALF14TB"),
        # "AB" is no canton, so this is not a plate and costs no billed call.
        ("AB1234", IdentifierKind.WERKSCODE, "AB1234"),
    ],
)
def test_the_input_decides_what_it_is(raw, kind, value):
    assert svc.classify_identifier(raw) == (kind, value)


def test_an_unrecognisable_input_is_a_422_not_a_guess(db_session):
    with pytest.raises(UnprocessableEntityError):
        svc.identify(db_session, tenant_id=uuid.uuid4(), actor_id=None, query="§§§")


def test_one_input_resolves_all_four_kinds_without_saying_which(client, db_session):
    """Exit criterion 1: the same endpoint, the same parameter, four kinds."""

    tenant_id = _connected_tenant(db_session)
    _mdm(db_session, vin="WVWZZZ1KZAW000001", stammnummer="555666777")
    headers = _bearer(tenant_id)

    def kind_of(q: str) -> str:
        response = client.get("/v1/vehicle-identification", params={"q": q}, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()["kind"]

    assert kind_of("WVWZZZ1KZAW000001") == "vin"
    assert kind_of("BE 123 456") == "kontrollschild"
    assert kind_of("555.666.777") == "stammnummer"
    assert kind_of("2CD456") == "typenschein"


# --- VIN ------------------------------------------------------------------


def test_a_vin_held_in_mdm_is_decisive_and_offers_the_configuration_for_reuse(db_session):
    tenant_id = _connected_tenant(db_session, seed=False)
    vehicle = _mdm(db_session, vin="WVWZZZ1KZAW000002")
    earlier = configuration_service.create_configuration(
        db_session, tenant_id=tenant_id, actor_id=uuid.uuid4(),
        data=ConfigurationCreate(
            source=ConfigurationSource.MANUAL, mode=ConfigurationMode.RECORD,
            match_method=ConfigurationMatchMethod.VIN, vin="WVWZZZ1KZAW000002",
        ),
    )
    db_session.commit()
    assert earlier.vehicle_id == vehicle.id

    result = svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="WVWZZZ1KZAW000002")

    assert result.outcome == IdentificationOutcome.EXISTING_VEHICLE
    assert result.existing_vehicle is not None
    assert result.existing_vehicle.vehicle_id == vehicle.id
    assert result.existing_vehicle.reusable_configuration_id == earlier.id
    # another tenant's configuration of the same car is never offered
    other = svc.identify(db_session, tenant_id=uuid.uuid4(), actor_id=None, query="WVWZZZ1KZAW000002")
    assert other.existing_vehicle is not None
    assert other.existing_vehicle.reusable_configuration_id is None


def test_without_the_vin_entitlement_a_vin_miss_falls_through_quietly(db_session):
    """Exit criterion 6: MDM, then fall through — no error, no provider call."""

    tenant_id = _connected_tenant(db_session, seed=False)
    calls_before = _all_calls(db_session)

    result = svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="WVWZZZ1KZAW000099")

    assert result.outcome == IdentificationOutcome.NONE
    assert result.observed.vin == "WVWZZZ1KZAW000099"
    assert result.notes == [IdentificationNote.VIN_DECODE_NOT_ENTITLED]
    assert _all_calls(db_session) == calls_before


def test_with_the_vin_entitlement_the_unspecified_decode_falls_through_too(db_session, monkeypatch):
    """The decode webservice has no specification (KAN-81): an entitled
    tenant falls through exactly like an unentitled one, and no failing
    call is logged against the connection's circuit breaker."""

    tenant_id = _connected_tenant(db_session, seed=False)
    monkeypatch.setattr(svc, "tenant_has_capability", lambda db, *, tenant_id, capability_code: True)
    calls_before = _all_calls(db_session)

    result = svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="WVWZZZ1KZAW000098")

    assert result.outcome == IdentificationOutcome.NONE
    assert result.notes == [IdentificationNote.VIN_DECODE_UNAVAILABLE]
    assert _all_calls(db_session) == calls_before


# --- Kontrollschild -------------------------------------------------------


def test_a_single_plate_record_continues_at_the_typenschein(db_session):
    tenant_id = _connected_tenant(db_session)

    result = svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="BE 123 456")

    assert result.outcome == IdentificationOutcome.VARIANTS
    assert result.match_method == ConfigurationMatchMethod.KONTROLLSCHILD
    assert [v.variant_name for v in result.variants] == ["Golf GTI 2.0 TSI DSG"]
    assert result.observed.licence_plate == "BE123456"
    assert result.observed.stammnummer == "777888999"
    assert result.observed.type_approval_number == "2CD456"
    assert result.observed.first_registration_date == dt.date(2022, 3, 1)


def test_a_wechselschild_is_a_legitimate_picker_and_nothing_is_selected(db_session):
    """Exit criteria 2 and 3."""

    tenant_id = _connected_tenant(db_session)
    configurations_before = db_session.scalar(select(func.count()).select_from(VehicleConfiguration))

    result = svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="ZH999999")

    assert result.outcome == IdentificationOutcome.PLATE_RECORDS
    assert len(result.plate_records) == 2
    assert result.plate_records_interchangeable is True
    assert result.plate_records_conflict is False
    assert result.variants == []
    assert db_session.scalar(select(func.count()).select_from(VehicleConfiguration)) == configurations_before
    assert db_session.scalar(
        select(func.count()).select_from(OutboxMessage).where(OutboxMessage.event_type == "vehicle.plate_lookup.conflicted")
    ) == 0


def test_a_genuine_plate_conflict_is_a_data_quality_event_and_still_a_picker(db_session):
    tenant_id = _connected_tenant(db_session)

    result = svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="GE111111")

    assert result.outcome == IdentificationOutcome.PLATE_RECORDS
    assert result.plate_records_conflict is True
    assert result.plate_records_interchangeable is False
    event = db_session.scalar(
        select(OutboxMessage).where(OutboxMessage.event_type == "vehicle.plate_lookup.conflicted")
    )
    assert event is not None
    assert event.tenant_id == tenant_id


def test_an_unknown_plate_says_so_and_is_not_cached(db_session):
    tenant_id = _connected_tenant(db_session, seed=False)

    result = svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="TI 1")

    assert result.outcome == IdentificationOutcome.NONE
    assert result.notes == [IdentificationNote.PLATE_NOT_FOUND]
    assert db_session.scalar(select(func.count()).select_from(PlateLookupCacheEntry)) == 0


def test_without_a_provider_connection_a_plate_degrades_to_none(db_session):
    result = svc.identify(db_session, tenant_id=uuid.uuid4(), actor_id=None, query="BE123456")

    assert result.outcome == IdentificationOutcome.NONE
    assert result.notes == [IdentificationNote.NO_PROVIDER_CONNECTION]


def test_a_plate_lookup_the_account_is_not_entitled_to_is_never_made(db_session):
    tenant_id = _connected_tenant(db_session, seed=False)
    connection = db_session.scalar(select(IntegrationConnection).where(IntegrationConnection.tenant_id == tenant_id))
    db_session.add(
        IntegrationEntitlement(
            connection_id=connection.id, capability_code="kontrollschild", granted=False,
            source=EntitlementSource.DECLARED, checked_at=utcnow(),
        )
    )
    db_session.commit()

    result = svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="BE123456")

    assert result.notes == [IdentificationNote.PLATE_LOOKUP_NOT_ENTITLED]
    assert _plate_calls(db_session, tenant_id) == 0


# --- the plate-lookup cache -----------------------------------------------


def test_the_plate_cache_turns_repeat_lookups_into_no_calls(db_session):
    """Exit criterion 5: three lookups of one plate, one billed call."""

    tenant_id = _connected_tenant(db_session)

    first = svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="BE123456")
    second = svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="be 123 456")
    third = svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="BE 123456")

    assert _plate_calls(db_session, tenant_id) == 1
    assert IdentificationNote.PLATE_CACHE_HIT not in first.notes
    assert second.notes == [IdentificationNote.PLATE_CACHE_HIT]
    assert third.variants == first.variants


def test_the_plate_cache_is_per_tenant(db_session):
    tenant_a = _connected_tenant(db_session)
    provider = db_session.scalar(select(IntegrationProvider).where(IntegrationProvider.provider_code == "auto_i_dat_mock"))
    tenant_b = uuid.uuid4()
    connection_service.create_connection(
        db_session, tenant_id=tenant_b,
        data=ConnectionCreate(provider_id=provider.id, display_name="b", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )

    svc.identify(db_session, tenant_id=tenant_a, actor_id=None, query="BE123456")
    svc.identify(db_session, tenant_id=tenant_b, actor_id=None, query="BE123456")

    assert _plate_calls(db_session, tenant_a) == 1
    assert _plate_calls(db_session, tenant_b) == 1


def test_a_cached_answer_past_its_ttl_is_fetched_again_and_then_purged(db_session):
    tenant_id = _connected_tenant(db_session)
    svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="BE123456")

    expired = utcnow() - plate_lookup_cache.PLATE_LOOKUP_CACHE_TTL - dt.timedelta(minutes=1)
    for row in db_session.scalars(select(PlateLookupCacheEntry)).all():
        row.fetched_at = expired
    db_session.commit()
    assert plate_lookup_cache.cached_records_for_plate(db_session, tenant_id=tenant_id, plate="BE123456") is None

    assert plate_lookup_cache.purge_expired_plate_lookups(db_session) == 1
    assert db_session.scalar(select(func.count()).select_from(PlateLookupCacheEntry)) == 0

    svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="BE123456")
    assert _plate_calls(db_session, tenant_id) == 2


def test_the_stated_ttl_is_thirty_days():
    assert plate_lookup_cache.PLATE_LOOKUP_CACHE_TTL == dt.timedelta(days=30)


# --- Stammnummer ----------------------------------------------------------


def test_a_stammnummer_resolves_against_mdm_first(db_session):
    tenant_id = _connected_tenant(db_session, seed=False)
    vehicle = _mdm(db_session, vin="WVWZZZ1KZAW000003", stammnummer="111000222")

    result = svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="111.000.222")

    assert result.outcome == IdentificationOutcome.EXISTING_VEHICLE
    assert result.existing_vehicle is not None and result.existing_vehicle.vehicle_id == vehicle.id


def test_a_stammnummer_resolves_through_an_earlier_plate_answer_and_never_calls(db_session):
    tenant_id = _connected_tenant(db_session)
    svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="BE123456")
    calls = _all_calls(db_session)

    result = svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="777888999")

    assert result.outcome == IdentificationOutcome.VARIANTS
    assert [v.variant_name for v in result.variants] == ["Golf GTI 2.0 TSI DSG"]
    assert _all_calls(db_session) == calls


def test_an_unknown_stammnummer_falls_through(db_session):
    result = svc.identify(db_session, tenant_id=uuid.uuid4(), actor_id=None, query="999999999")
    assert result.outcome == IdentificationOutcome.NONE


# --- Typenschein / Werkscode ---------------------------------------------


def test_a_typenschein_shared_by_two_variants_is_a_picker_with_best_match_offered(db_session):
    _variant_sharing_type_approval(db_session, "9ZZ001", "Golf 1.5 TSI")
    _variant_sharing_type_approval(db_session, "9ZZ001", "Golf 2.0 TDI")
    configurations_before = db_session.scalar(select(func.count()).select_from(VehicleConfiguration))

    result = svc.identify(db_session, tenant_id=uuid.uuid4(), actor_id=None, query="9ZZ001")

    assert result.outcome == IdentificationOutcome.VARIANTS
    assert sorted(v.variant_name for v in result.variants) == ["Golf 1.5 TSI", "Golf 2.0 TDI"]
    assert result.best_match_available is True
    assert db_session.scalar(select(func.count()).select_from(VehicleConfiguration)) == configurations_before


def test_a_typenschein_with_one_variant_is_decisive(db_session):
    tenant_id = _connected_tenant(db_session)
    result = svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="2CD456")
    assert len(result.variants) == 1
    assert result.best_match_available is False


def test_a_werkscode_resolves_against_the_mirror(db_session):
    tenant_id = _connected_tenant(db_session)
    calls = _all_calls(db_session)
    result = svc.identify(db_session, tenant_id=tenant_id, actor_id=None, query="VW20TSI")
    assert [v.variant_name for v in result.variants] == ["Golf GTI 2.0 TSI DSG"]
    assert _all_calls(db_session) == calls


# --- best match -----------------------------------------------------------


def test_a_best_match_is_a_proposal_and_writes_no_configuration(db_session):
    """Exit criterion 4, first half: nothing is applied by asking."""

    tenant_id = _connected_tenant(db_session)
    configurations_before = db_session.scalar(select(func.count()).select_from(VehicleConfiguration))

    proposal = svc.propose_best_match(
        db_session, tenant_id=tenant_id, actor_id=None, type_approval_number="2CD456",
        new_price=Decimal(48900), new_price_source=NewPriceSource.DOCUMENT,
    )

    assert proposal.requires_confirmation is True
    assert proposal.match_code == 2
    assert proposal.candidate.variant_name == "Golf GTI 2.0 TSI DSG"
    assert proposal.new_price_source == NewPriceSource.DOCUMENT
    assert {f.field for f in proposal.fields} == {"typeApprovalNumber", "newPrice"}
    assert db_session.scalar(select(func.count()).select_from(VehicleConfiguration)) == configurations_before


def test_a_unique_best_match_still_needs_confirmation(db_session):
    tenant_id = _connected_tenant(db_session)
    proposal = svc.propose_best_match(
        db_session, tenant_id=tenant_id, actor_id=None, type_approval_number="2CD456",
        new_price=Decimal(48900), new_price_source=NewPriceSource.CATALOGUE, model_description="Golf GTI",
    )
    assert proposal.match_code == 1
    assert proposal.requires_confirmation is True


def test_confirming_a_best_match_records_best_match_confirmed(db_session):
    """Exit criterion 4, second half: confirming is what writes the status."""

    tenant_id = _connected_tenant(db_session)
    proposal = svc.propose_best_match(
        db_session, tenant_id=tenant_id, actor_id=None, type_approval_number="2CD456",
        new_price=Decimal(48900), new_price_source=NewPriceSource.DOCUMENT,
    )

    config = configuration_service.create_configuration(
        db_session, tenant_id=tenant_id, actor_id=uuid.uuid4(),
        data=ConfigurationCreate(
            source=ConfigurationSource.PROVIDER, mode=ConfigurationMode.RECORD,
            match_method=ConfigurationMatchMethod.TYPENSCHEIN,
            catalogue_variant_id=proposal.candidate.catalogue_variant_id,
            confirmed_best_match_code=proposal.match_code, type_approval_number="2CD456",
        ),
    )

    assert config.catalogue_match_status == ConfigurationMatchStatus.BEST_MATCH_CONFIRMED


def test_a_best_match_code_on_a_manual_configuration_is_refused(db_session):
    with pytest.raises(UnprocessableEntityError):
        configuration_service.create_configuration(
            db_session, tenant_id=uuid.uuid4(), actor_id=uuid.uuid4(),
            data=ConfigurationCreate(
                source=ConfigurationSource.MANUAL, mode=ConfigurationMode.RECORD,
                match_method=ConfigurationMatchMethod.MANUAL, confirmed_best_match_code=2,
            ),
        )


def test_best_match_without_a_provider_is_a_422(db_session):
    with pytest.raises(UnprocessableEntityError):
        svc.propose_best_match(
            db_session, tenant_id=uuid.uuid4(), actor_id=None, type_approval_number="2CD456",
            new_price=Decimal(1), new_price_source=NewPriceSource.CUSTOMER,
        )


def test_best_match_api_round_trip(client, db_session):
    tenant_id = _connected_tenant(db_session)
    response = client.get(
        "/v1/vehicle-identification/best-match",
        params={"typeApprovalNumber": "2CD456", "newPrice": "48900", "newPriceSource": "document"},
        headers=_bearer(tenant_id),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requiresConfirmation"] is True
    assert body["matchCode"] == 2
    assert body["candidate"]["variantName"] == "Golf GTI 2.0 TSI DSG"


# --- access ---------------------------------------------------------------


def test_identification_needs_configuration_write_access(client, db_session):
    tenant_id = _connected_tenant(db_session, seed=False)
    response = client.get(
        "/v1/vehicle-identification", params={"q": "BE123456"}, headers=_bearer(tenant_id, AccessRole.AUDITOR)
    )
    assert response.status_code == 403
    assert _plate_calls(db_session, tenant_id) == 0
