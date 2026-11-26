"""Whether work has been clocked (KAN-810; tables 3.1 to 3.3; check 9).

Three refusals of the slice turn on a clock: an order on which a clock has
run cannot be cancelled (decision 51: its jobs are declined instead), and a
job or a position with clocked time cannot be deleted (decision 64). The
clock is the technician's (KAN-814, AS1-11) and does not exist yet, so in
this slice no clock has ever run and these answer «no».

They are the one place the rules ask. KAN-814 replaces their bodies with a
read of its clock entries; no rule and no route changes then. Each is a
plain read on the caller's session.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session


def clock_has_run_on_order(db: Session, *, tenant_id: uuid.UUID, order_id: uuid.UUID) -> bool:
    """A clock entry exists on any position of the order, running or stopped."""

    return False


def time_clocked_on_job(db: Session, *, tenant_id: uuid.UUID, job_id: uuid.UUID) -> bool:
    """A clock entry exists on any position of the job."""

    return False


def time_clocked_on_position(db: Session, *, tenant_id: uuid.UUID, position_id: uuid.UUID) -> bool:
    """A clock entry exists on the position."""

    return False
