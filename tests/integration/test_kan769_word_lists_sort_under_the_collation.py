"""KAN-769 (ADR-190): the integration context's word lists sort under the collation — case
ignored, Ä/é beside A/e — not in the test database's byte order (`C.UTF-8`). Each expected
order is the database's own (`tests.text_sort_support.text_sort_order`)."""

import uuid

from app.core.base import utcnow
from app.integration.models.connection import (
    ConnectionEnvironment,
    ConnectionScope,
    ConnectionStatus,
    IntegrationConnection,
)
from app.integration.models.google_business_profile import GbpBindingStatus, GbpLocationBinding
from app.integration.models.provider import IntegrationProvider
from app.integration.services.gbp_link import list_bindings
from app.integration.services.providers import list_providers
from app.integration.services.status import IntegrationStatusProvider
from tests.postgres_only import POSTGRES_SQL
from tests.text_sort_support import text_sort_order

NAMES = ["Zeta", "alpha", "Ärztin", "Arzt", "école"]


def _collated(db_session, values: list[str]) -> list[str]:
    """The collated order, checked to differ from byte order — else the test proves nothing."""

    expected = text_sort_order(db_session, values)
    assert expected != sorted(values), "byte order and collated order must differ"
    return expected


def _provider(db, display_name: str) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code=f"p_{uuid.uuid4().hex[:8]}", category="vehicle_data", display_name=display_name,
        auth_type="none", required_secret_slots=[], capability_codes=[],
    )
    db.add(provider)
    db.flush()
    return provider


def _connection(db, provider, tenant_id, display_name, environment=ConnectionEnvironment.PRODUCTION) -> None:
    db.add(
        IntegrationConnection(
            provider_id=provider.id, scope=ConnectionScope.TENANT, tenant_id=tenant_id, display_name=display_name,
            environment=environment, config={}, enabled=True, status=ConnectionStatus.CONNECTED,
        )
    )


@POSTGRES_SQL
def test_kan769_list_providers_sorts_by_display_name_under_the_collation(db_session):
    for name in NAMES:
        _provider(db_session, name)
    db_session.commit()

    assert [provider.display_name for provider in list_providers(db_session)] == _collated(db_session, NAMES)


@POSTGRES_SQL
def test_kan769_status_items_sort_by_provider_then_connection_name_under_the_collation(db_session):
    tenant_id = uuid.uuid4()
    for name in NAMES:
        _connection(db_session, _provider(db_session, name), tenant_id, f"Verbindung {name}")
    shared = _provider(db_session, "Mittel")
    _connection(db_session, shared, tenant_id, "Zürich", ConnectionEnvironment.PRODUCTION)
    _connection(db_session, shared, tenant_id, "basel", ConnectionEnvironment.SANDBOX)
    db_session.commit()

    items = IntegrationStatusProvider().items(db_session, dealership_id=tenant_id, now=utcnow())

    providers = [item.params["provider"] for item in items]
    assert [name for name in providers if name != "Mittel"] == _collated(db_session, NAMES)
    assert providers == text_sort_order(db_session, providers)
    assert [item.params["connection"] for item in items if item.params["provider"] == "Mittel"] == _collated(
        db_session, ["Zürich", "basel"]
    )


@POSTGRES_SQL
def test_kan769_gbp_bindings_sort_by_location_label_under_the_collation(db_session):
    tenant_id = uuid.uuid4()
    now = utcnow()
    db_session.add_all(
        GbpLocationBinding(
            tenant_id=tenant_id, location_id=uuid.uuid4(), location_label=label, location_label_refreshed_at=now,
            status=GbpBindingStatus.PENDING, connected_at=now,
        )
        for label in NAMES
    )
    db_session.commit()

    labels = [binding.location_label for binding in list_bindings(db_session, dealership_id=tenant_id)]

    assert labels == _collated(db_session, NAMES)
