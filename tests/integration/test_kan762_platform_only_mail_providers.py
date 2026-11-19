"""KAN-762: the platform-only rule (KAN-215) is keyed on the two mailbox providers, not on their
`calendar_mail` category, and agrees with `LOCATION_BINDABLE_CATEGORIES` (KAN-222): a Betrieb
may hold — and bind to one of its locations — a calendar provider's connection of its own; it
never holds a Microsoft 365 or Google Workspace mail connection."""

import pytest

from app.core.errors import UnprocessableEntityError
from app.integration.models.mail import MailProviderCode
from app.integration.services import connections as connection_service
from tests.integration.test_integration_connection_location import (
    _connection,
    _dealership,
    _location,
    _manager,
    _patch,
    _provider,
)


@pytest.mark.parametrize("code", [code.value for code in MailProviderCode])
def test_a_mailbox_provider_is_platform_only_and_never_location_bindable(db_session, code):
    dealership = _dealership(db_session)
    provider = _provider(db_session, code=code, category="calendar_mail", config_keys=[])

    assert connection_service.is_platform_only(provider)
    assert not connection_service.is_location_bindable(provider)
    with pytest.raises(UnprocessableEntityError) as refused:
        _connection(db_session, provider, dealership, {})
    assert refused.value.details["reason"] == "mail_provider_platform_only"


def test_a_site_calendar_provider_is_a_betriebs_own_and_bound_to_its_location(client, db_session):
    dealership = _dealership(db_session)
    location = _location(db_session, dealership, "Zürich Nord")
    provider = _provider(db_session, code="site_calendar", category="calendar_mail", config_keys=[])

    assert not connection_service.is_platform_only(provider)
    assert connection_service.is_location_bindable(provider)
    connection = _connection(db_session, provider, dealership, {})
    assert connection.tenant_id == dealership.id

    response = _patch(client, connection.id, connection.version, {"locationId": str(location.id)}, _manager(dealership))

    assert response.status_code == 200, response.text
    assert response.json()["locationLabel"] == "Zürich Nord"
    assert response.json()["locationBindable"] is True
