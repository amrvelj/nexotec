"""KAN-221 PR-1 test support: the platform tenant and the staff it homes.

The platform migration creates the platform tenant (group «Nexotec» and its
Betrieb of kind `platform`); the test schema is built from the models and
emptied before every test, so a test that needs it asks for it here — with
the migration's own values (`tests/platform/test_kan221_platform_tenant.py`
holds them equal).

`create_staff` is Nexotec staff as they are from now on: homed in the
platform tenant. `create_legacy_staff` is a staff account as it still exists
from before (KAN-199's report): homed in a dealer's Betrieb — granted on the
row, since the API no longer grants `platform_admin` there.
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import update

from app.db import get_session_factory
from app.platform.identity.models.user import User
from app.platform.organisation.models.dealership import (
    PLATFORM_DEALER_GROUP_ID,
    PLATFORM_DEALERSHIP_ID,
    PLATFORM_TENANT_NAME,
    DealerGroup,
    Dealership,
    DealershipKind,
    DealershipStatus,
    FranchiseType,
)
from tests.identity_support import create_user, platform_admin_headers


def _session(client: TestClient):
    return client.app.dependency_overrides[get_session_factory]()()


def platform_tenant_rows() -> tuple[DealerGroup, Dealership]:
    """The two rows exactly as the migration writes them."""

    group = DealerGroup(id=PLATFORM_DEALER_GROUP_ID, name=PLATFORM_TENANT_NAME, group_read_enabled=False)
    dealership = Dealership(
        id=PLATFORM_DEALERSHIP_ID,
        dealer_group_id=PLATFORM_DEALER_GROUP_ID,
        kind=DealershipKind.PLATFORM,
        legal_name=PLATFORM_TENANT_NAME,
        franchise_type=FranchiseType.INDEPENDENT,
        address_street="",
        address_house_number="",
        address_postal_code="",
        address_locality="",
        address_canton="",
        address_country="CH",
        phone="",
        vat_liable=False,
        status=DealershipStatus.ACTIVE,
    )
    return group, dealership


def ensure_platform_tenant(client: TestClient) -> str:
    """The platform tenant's Betrieb id, its two rows created when missing."""

    with _session(client) as db:
        if db.get(Dealership, PLATFORM_DEALERSHIP_ID) is None:
            group, dealership = platform_tenant_rows()
            db.add(group)
            db.flush()
            db.add(dealership)
            db.commit()
    return str(PLATFORM_DEALERSHIP_ID)


def create_staff(client: TestClient, **overrides) -> dict:
    """A Nexotec staff account, homed in the platform tenant."""

    payload = {"accessRoles": ["platform_admin"], "isDealerManager": False, "role": "admin", **overrides}
    return create_user(client, ensure_platform_tenant(client), **payload)


def create_legacy_staff(client: TestClient, dealership_id: str, **overrides) -> dict:
    """A staff account homed in a dealer's Betrieb, as KAN-199 found them —
    created as a plain user, then given `platform_admin` on the row."""

    user = create_user(client, dealership_id, **{"isDealerManager": False, **overrides})
    with _session(client) as db:
        db.execute(update(User).where(User.id == uuid.UUID(user["id"])).values(access_roles=["platform_admin"]))
        db.commit()
    response = client.get(f"/v1/dealerships/{dealership_id}/users/{user['id']}", headers=platform_admin_headers())
    assert response.status_code == 200, response.text
    return response.json()
