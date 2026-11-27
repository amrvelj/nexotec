"""Whether work has been clocked (KAN-810; tables 3.1 to 3.3; check 9).

Three refusals of the slice turn on a clock: an order on which a clock has
run cannot be cancelled (decision 51: its jobs are declined instead), and a
job or a position with clocked time cannot be deleted (decision 64). The
clock is the technician's (KAN-814, AS1-11) and does not exist yet, so in
this slice no clock has ever run and these answer «no».

KAN-811 adds the two questions of the customer's answer (table 3.2, checks
6 and 8): who is clocked on a job right now, and stopping a job's clocks —
both empty until KAN-814 gives them a body.

They are the one place the rules ask. KAN-814 replaces their bodies with a
read of its clock entries; no rule and no route changes then. Each is a
plain read on the caller's session.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class ClockedPerson:
    """A person with a clock on a job: whom the order names before a change (check 6)."""

    user_id: uuid.UUID
    label: str


def clock_has_run_on_order(db: Session, *, tenant_id: uuid.UUID, order_id: uuid.UUID) -> bool:
    """A clock entry exists on any position of the order, running or stopped."""

    return False


def time_clocked_on_job(db: Session, *, tenant_id: uuid.UUID, job_id: uuid.UUID) -> bool:
    """A clock entry exists on any position of the job."""

    return False


def time_clocked_on_position(db: Session, *, tenant_id: uuid.UUID, position_id: uuid.UUID) -> bool:
    """A clock entry exists on the position."""

    return False


def people_clocked_on_job(db: Session, *, tenant_id: uuid.UUID, job_id: uuid.UUID) -> list[ClockedPerson]:
    """The people whose clock runs on a position of the job now (ruling 49, check 6)."""

    return []


def stop_clocks_on_job(
    db: Session, *, tenant_id: uuid.UUID, job_id: uuid.UUID, actor_id: uuid.UUID
) -> list[ClockedPerson]:
    """Stops every running clock on the job and answers whose it was, so they are told
    (ruling 48, check 8). Inside the caller's transaction; does not commit."""

    return []


def clock_running_on_order(db: Session, *, tenant_id: uuid.UUID, order_id: uuid.UUID) -> bool:
    """A clock runs on a position of the order now (KAN-813: «in work», section 3.9)."""

    return False
