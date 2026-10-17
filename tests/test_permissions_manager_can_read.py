"""KAN-198: `Capability.manager_can_read` — the platform-only and self-only
rows of the permission matrix are not readable by the dealer manager, whose
flag otherwise opens every read. Platform staff read everything; a listed
read role still reads its row."""

import uuid

import pytest

from app.core.auth import AccessRole, Principal
from app.core.errors import ForbiddenError
from app.core.permissions import CAPABILITY_MATRIX, require_read


def _principal(*roles: AccessRole, is_dealer_manager: bool = False) -> Principal:
    tenant_id = uuid.uuid4()
    return Principal(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=uuid.uuid4(),
        memberships=frozenset({tenant_id}),
        active_location_id=None,
        roles=frozenset(roles),
        is_dealer_manager=is_dealer_manager,
    )


def _rows_where(manager_can_read: bool) -> list[str]:
    return sorted(key for key, cap in CAPABILITY_MATRIX.items() if cap.manager_can_read is manager_can_read)


def test_the_matrix_has_rows_the_manager_cannot_read():
    assert "recovery_actions" in _rows_where(False)
    assert "module_entitlements" in _rows_where(True)


@pytest.mark.parametrize("key", _rows_where(False))
def test_the_manager_cannot_read_a_platform_only_row(key):
    with pytest.raises(ForbiddenError):
        require_read(key)(_principal(is_dealer_manager=True))


@pytest.mark.parametrize("key", _rows_where(False))
def test_platform_staff_read_every_platform_only_row(key):
    principal = _principal(AccessRole.PLATFORM_ADMIN)
    assert require_read(key)(principal) is principal


def test_the_manager_reads_a_manager_row():
    principal = _principal(is_dealer_manager=True)
    assert require_read("module_entitlements")(principal) is principal


def test_a_plain_role_without_the_flag_is_refused_on_a_manager_row():
    with pytest.raises(ForbiddenError):
        require_read("module_entitlements")(_principal(AccessRole.SALES))
