"""KAN-787 (Anto, 2026-11-22: option B): the providers Nexotec connects once for every Betrieb
are not shown to a dealer.

The mailbox providers and Google Business Profile are platform-only
(`services/connections.py::PLATFORM_ONLY_PROVIDER_CODES`, KAN-215/KAN-226): a Betrieb's own
connection is refused with 422. Their cards still offered «Verbinden» on the dealer's
Integrationen screen. The catalogue now says which providers are platform-only
(`platformOnly`), from that one list, and the dealer view hides them
(`IntegrationDealerView.categories.render.test.tsx`).
"""

import uuid

from app.integration.models.google_business_profile import GBP_PROVIDER_CODE
from app.integration.models.provider import IntegrationProvider
from app.integration.services.connections import PLATFORM_ONLY_PROVIDER_CODES
from tests.integration.test_integration_idempotency import _manager_token


def test_the_catalogue_flags_exactly_the_platform_only_providers(client, db_session):
    codes = sorted({*PLATFORM_ONLY_PROVIDER_CODES, "autoscout24", "auto_i_dat_mock"})
    assert {"google_workspace_mail", "microsoft_365_mail", GBP_PROVIDER_CODE} <= set(codes)
    db_session.add_all(
        IntegrationProvider(provider_code=code, category="marketplace", display_name=code, auth_type="none")
        for code in codes
    )
    db_session.commit()

    response = client.get(
        "/v1/integrations/providers", headers={"Authorization": f"Bearer {_manager_token(uuid.uuid4())}"}
    )

    assert response.status_code == 200, response.text
    flags = {item["providerCode"]: item["platformOnly"] for item in response.json()["items"]}
    assert flags == {code: code in PLATFORM_ONLY_PROVIDER_CODES for code in codes}
