"""The dealer group a test token carries (KAN-424).

A real session carries its Betrieb's own dealer group: `_mint_session`
(`app/platform/api/auth.py`) is the only app caller of `create_access_token`,
and it passes `dealership.dealer_group_id`. A customer is stored with the
token's `group_id`, and the customer hand-over compares it with the groups a
colleague works in (KAN-423), so a test token with a made-up group makes
every group-scoped check pass or fail for the wrong reason
(`.claude/rules/tests.md`, "Know what your fixture sets").

`dealer_group_of` reads the Betrieb's `dealer_group_id` from the test
database, once per Betrieb and test. The autouse `_token_dealer_groups`
fixture in `tests/conftest.py` hands it the test's engine and clears the
cache after the test (every test starts with empty tables).

A tenant id that names no Betrieb (a token minted before the Betrieb is
created, or for a tenant a test never creates) has no real group, and no
real session exists for it; such a token carries `synthetic_group_of`, the
uuid5 the helpers minted before. It is never cached, so a token minted once
the Betrieb exists carries its real group.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.platform.organisation.models.dealership import Dealership

_ENGINE: list[Engine] = []
_GROUPS: dict[uuid.UUID, uuid.UUID] = {}


def bind(engine: Engine | None) -> None:
    """Called by the conftest fixture: the engine this test's tables live on
    (None after the test)."""

    _ENGINE[:] = [engine] if engine is not None else []
    _GROUPS.clear()


def synthetic_group_of(tenant_id: uuid.UUID | str) -> uuid.UUID:
    """The made-up group for a tenant that has no Betrieb row."""

    return uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id))


def dealer_group_of(tenant_id: uuid.UUID | str) -> uuid.UUID:
    """The dealer group of the Betrieb `tenant_id`, as a minted session
    carries it; `synthetic_group_of` while no such Betrieb exists."""

    tid = uuid.UUID(str(tenant_id))
    if tid in _GROUPS:
        return _GROUPS[tid]
    if not _ENGINE:
        return synthetic_group_of(tid)
    with _ENGINE[0].connect() as conn:
        group = conn.execute(select(Dealership.dealer_group_id).where(Dealership.id == tid)).scalar_one_or_none()
    if group is None:
        return synthetic_group_of(tid)
    _GROUPS[tid] = group
    return group
