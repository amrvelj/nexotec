"""WP-7 PR-7: group listing API — /groups/mine resolves group_id from the
JWT, never a client-supplied one."""

import uuid

from app.core.auth import AccessRole, create_access_token
from app.platform.models.dealership import DealerGroup


def _token(group_id: uuid.UUID, role: AccessRole | None = None) -> str:
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=group_id,
        roles=frozenset({role}) if role else frozenset(),
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_groups_mine_404s_when_group_read_not_enabled(client, db_session):
    group = DealerGroup(name="Read-disabled", group_read_enabled=False)
    db_session.add(group)
    db_session.commit()

    token = _token(group.id, AccessRole.INVENTORY)
    response = client.get("/v1/inventory/groups/mine/stock-items", headers=_bearer(token))
    assert response.status_code == 404, response.text


def test_groups_mine_200s_when_enabled_even_with_no_stock(client, db_session):
    group = DealerGroup(name="Read-enabled", group_read_enabled=True)
    db_session.add(group)
    db_session.commit()

    token = _token(group.id, AccessRole.INVENTORY)
    response = client.get("/v1/inventory/groups/mine/stock-items", headers=_bearer(token))
    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}
