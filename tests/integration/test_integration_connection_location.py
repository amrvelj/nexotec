"""KAN-222 (FR-A-20, FR-A-21): a connection's card names the account at the
provider and the location it serves.

- The account identifier is derived from the non-secret configuration, per
  provider; a configuration lacking it reads as null («Kennung fehlt»).
- A marketplace connection may be bound to one of its own Betrieb's
  locations; another Betrieb's location, an unknown one, a vehicle-data
  connection and a platform-wide connection are refused (422). The location's
  name is copied beside the id (rule 2) and refreshed nightly.
"""

import datetime as dt
import uuid

from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import create_access_token
from app.integration.models.connection import ConnectionEnvironment, IntegrationConnection
from app.integration.models.provider import IntegrationProvider
from app.integration.public import oldest_location_label_age_seconds, refresh_location_labels
from app.integration.reconciliation import CHECKS
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services.account_identifier import ACCOUNT_IDENTIFIER_CONFIG_KEYS, account_identifier
from app.integration.services.gateway import _ADAPTER_FACTORIES
from app.platform.organisation.models.dealership import DealerGroup, Dealership, FranchiseType, Location
from tests.dealership_support import valid_uid
from tests.token_support import dealer_group_of


def _dealership(db_session) -> Dealership:
    group = DealerGroup(name=f"Group {uuid.uuid4().hex[:6]}")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        dealer_group_id=group.id, legal_name="Garage Muster AG", franchise_type=FranchiseType.INDEPENDENT,
        address_street="Bahnhofstrasse", address_house_number="1", address_postal_code="8001",
        address_locality="Zürich", address_canton="ZH", phone="+41441234567", uid=valid_uid(),
    )
    db_session.add(dealership)
    db_session.commit()
    return dealership


def _location(db_session, dealership: Dealership, name: str) -> Location:
    location = Location(tenant_id=dealership.id, name=name, address_locality="Zürich", location_types=[])
    db_session.add(location)
    db_session.commit()
    return location


def _provider(db_session, *, code: str, category: str, config_keys: list[str]) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code=code, category=category, display_name=code, auth_type="password",
        required_secret_slots=[], required_config_keys=config_keys, capability_codes=[],
    )
    db_session.add(provider)
    db_session.commit()
    return provider


def _connection(db_session, provider, dealership, config) -> IntegrationConnection:
    return connection_service.create_connection(
        db_session, tenant_id=dealership.id,
        data=ConnectionCreate(
            provider_id=provider.id, display_name=provider.display_name,
            environment=ConnectionEnvironment.PRODUCTION, config=config,
        ),
        actor_id=None,
    )


def _manager(dealership: Dealership) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=dealership.id, group_id=dealer_group_of(str(dealership.id)),
        roles=frozenset(), is_dealer_manager=True,
    )
    return {"Authorization": f"Bearer {token}"}


def _marketplace(db_session, dealership):
    provider = _provider(
        db_session, code="autoscout24", category="marketplace", config_keys=["kundennummer", "ftpHost", "ftpUsername"]
    )
    return _connection(
        db_session, provider, dealership, {"kundennummer": "K-4711", "ftpHost": "ftp.example.test", "ftpUsername": "u"}
    )


def _patch(client, connection_id, version, body, headers):
    return client.patch(
        f"/v1/integrations/connections/{connection_id}", json=body, headers={**headers, "If-Match": str(version)}
    )


# --- account identifier -------------------------------------------------------------


def test_account_identifier_is_derived_from_the_configuration_per_provider():
    assert account_identifier("auto_i_dat", {"clientId": "svc-garage"}) == "svc-garage"
    assert account_identifier("auto_i_dat_mock", {"username": "u", "benutzerNr": "12", "benutzerInfo": "Werk"}) == (
        "12 · Werk"
    )
    assert account_identifier("autoscout24", {"kundennummer": "K-4711", "ftpUsername": "u"}) == "K-4711"


def test_account_identifier_is_null_when_the_configuration_lacks_it():
    assert account_identifier("auto_i_dat", {}) is None
    assert account_identifier("auto_i_dat", {"clientId": "  "}) is None
    assert account_identifier("unknown_provider", {"clientId": "x"}) is None


def test_every_provider_with_an_adapter_names_its_account_identifier():
    # The adapters register their factories on import (app/main imports them).
    import app.main  # noqa: F401

    missing = sorted(set(_ADAPTER_FACTORIES) - set(ACCOUNT_IDENTIFIER_CONFIG_KEYS))
    assert missing == [], f"providers with an adapter and no account identifier: {missing}"


def test_the_card_carries_the_account_identifier(client, db_session):
    dealership = _dealership(db_session)
    connection = _marketplace(db_session, dealership)

    body = client.get(f"/v1/integrations/connections/{connection.id}", headers=_manager(dealership)).json()

    assert body["accountIdentifier"] == "K-4711"
    assert body["locationBindable"] is True
    assert body["locationId"] is None and body["locationLabel"] is None


# --- location binding ----------------------------------------------------------------


def test_a_marketplace_connection_is_bound_to_its_own_location(client, db_session):
    dealership = _dealership(db_session)
    location = _location(db_session, dealership, "Zürich Nord")
    connection = _marketplace(db_session, dealership)

    response = _patch(client, connection.id, connection.version, {"locationId": str(location.id)}, _manager(dealership))

    assert response.status_code == 200, response.text
    assert response.json()["locationId"] == str(location.id)
    assert response.json()["locationLabel"] == "Zürich Nord"
    db_session.expire_all()
    row = db_session.get(IntegrationConnection, connection.id)
    assert row.location_label_refreshed_at is not None
    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == connection.id, AuditEvent.action == "bind_location")
    ).all()
    assert len(audit) == 1 and audit[0].after == {"locationId": str(location.id)}

    unbound = _patch(client, connection.id, response.json()["version"], {"locationId": None}, _manager(dealership))
    assert unbound.status_code == 200, unbound.text
    assert unbound.json()["locationId"] is None and unbound.json()["locationLabel"] is None


def test_another_betriebs_location_is_refused_like_an_unknown_one(client, db_session):
    dealership = _dealership(db_session)
    other = _dealership(db_session)
    foreign = _location(db_session, other, "Bern")
    connection = _marketplace(db_session, dealership)

    for location_id in (foreign.id, uuid.uuid4()):
        response = _patch(client, connection.id, connection.version, {"locationId": str(location_id)}, _manager(dealership))
        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"]["reason"] == "unknown_location"
    db_session.expire_all()
    assert db_session.get(IntegrationConnection, connection.id).location_id is None


def test_a_vehicle_data_connection_is_never_bound(client, db_session):
    dealership = _dealership(db_session)
    location = _location(db_session, dealership, "Zürich Nord")
    provider = _provider(db_session, code="auto_i_dat", category="vehicle_data", config_keys=["clientId"])
    connection = _connection(db_session, provider, dealership, {"clientId": "svc"})

    response = _patch(client, connection.id, connection.version, {"locationId": str(location.id)}, _manager(dealership))

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "location_binding_not_supported"
    assert client.get(
        f"/v1/integrations/connections/{connection.id}", headers=_manager(dealership)
    ).json()["locationBindable"] is False


def test_leaving_location_out_of_a_patch_keeps_the_binding(client, db_session):
    dealership = _dealership(db_session)
    location = _location(db_session, dealership, "Zürich Nord")
    connection = _marketplace(db_session, dealership)
    bound = _patch(client, connection.id, connection.version, {"locationId": str(location.id)}, _manager(dealership)).json()

    renamed = _patch(client, connection.id, bound["version"], {"displayName": "AS24 Nord"}, _manager(dealership))

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["locationId"] == str(location.id)


# --- the nightly label refresh -------------------------------------------------------


def test_the_label_follows_a_renamed_location(client, db_session):
    dealership = _dealership(db_session)
    location = _location(db_session, dealership, "Zürich Nord")
    connection = _marketplace(db_session, dealership)
    _patch(client, connection.id, connection.version, {"locationId": str(location.id)}, _manager(dealership))
    db_session.expire_all()
    version_before = db_session.get(IntegrationConnection, connection.id).version
    location = db_session.get(Location, location.id)
    location.name = "Zürich Oerlikon"
    db_session.commit()

    assert refresh_location_labels(db_session) == 1

    db_session.expire_all()
    row = db_session.get(IntegrationConnection, connection.id)
    assert row.location_label == "Zürich Oerlikon"
    assert row.version == version_before  # a copy no client writes: no version bump
    assert refresh_location_labels(db_session) == 0  # current: only restamped
    age = oldest_location_label_age_seconds(db_session)
    assert age is not None and age < 60


def test_the_refresh_leaves_another_betriebs_location_alone(db_session):
    dealership = _dealership(db_session)
    other = _dealership(db_session)
    foreign = _location(db_session, other, "Bern")
    connection = _marketplace(db_session, dealership)
    stale = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    row = db_session.get(IntegrationConnection, connection.id)
    row.location_id, row.location_label, row.location_label_refreshed_at = foreign.id, "Old", stale
    db_session.commit()

    assert refresh_location_labels(db_session) == 0

    db_session.expire_all()
    row = db_session.get(IntegrationConnection, connection.id)
    assert row.location_label == "Old" and row.location_label_refreshed_at == stale


def test_reconciliation_checks_the_location_reference():
    assert "integration_connection.location_id -> location.id" in {check.label for check in CHECKS}
