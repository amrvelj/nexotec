import uuid

import pytest

from app.core.errors import NotFoundError
from app.core.tenancy import get_or_404
from tests.demo_models import DemoWidget


def test_get_or_404_returns_object_within_tenant(db_session):
    tenant_id = uuid.uuid4()
    widget = DemoWidget(tenant_id=tenant_id, name="Alpha")
    db_session.add(widget)
    db_session.commit()

    found = get_or_404(db_session, DemoWidget, widget.id, tenant_id)
    assert found.id == widget.id


def test_get_or_404_raises_not_found_for_cross_tenant_access(db_session):
    """Dealer A must not be able to read Dealer B's records via any lookup —
    and the failure mode must be 404, not 403 (spec acceptance criterion 7 +
    Round 3 addendum's cross-tenant-isolation rule).
    """
    owner_tenant = uuid.uuid4()
    other_tenant = uuid.uuid4()
    widget = DemoWidget(tenant_id=owner_tenant, name="Alpha")
    db_session.add(widget)
    db_session.commit()

    with pytest.raises(NotFoundError):
        get_or_404(db_session, DemoWidget, widget.id, other_tenant)


def test_get_or_404_raises_not_found_for_unknown_id(db_session):
    with pytest.raises(NotFoundError):
        get_or_404(db_session, DemoWidget, uuid.uuid4(), uuid.uuid4())
