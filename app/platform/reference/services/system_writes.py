"""The one documented system path into platform's create services (KAN-345).

In `reference`, the lowest sub-package (ADR-163 (5), KAN-635): `create_dealership`
(organisation) and `create_user` (identity) both call it, so it cannot live in
identity's `actor_reference.py`, where organisation would import upward.
"""

import uuid


def system_write_reason(actor_id: uuid.UUID | None, system_reason: str | None) -> str | None:
    """KAN-345 (Anto, 2026-11-03, option A): the one documented system path
    into a create service — `create_dealership`, `create_user`. A seed or a
    script that runs before any user exists writes with `actor_id=None` and a
    `system_reason`; the rows and the `create` audit row then carry no actor,
    and the audit row's `reason` says which system wrote it. A caller passes
    exactly one of the two: an actor with a system reason, or neither, is a
    programming error (ValueError), never a silent system write. No route
    takes this path — the routes pass the principal's id, and
    tests/architecture/test_system_actor_only_from_scripts.py keeps
    `system_reason` out of `app/`. Returns the reason for the audit row."""

    if actor_id is None and not (system_reason and system_reason.strip()):
        raise ValueError("A write without an actor is a system write and must name its system_reason (KAN-345).")
    if actor_id is not None and system_reason is not None:
        raise ValueError("A system_reason is for a write without an actor; this one names actor_id (KAN-345).")
    return system_reason
