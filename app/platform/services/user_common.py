"""What the user service modules share, split from `user.py` (KAN-669, a pure
move): the status sets and the two predicates — an active user, an active
membership — that both the reads (`user_reads.py`) and the lifecycle rules
and writes (`user.py`) use. Imports neither of them.
"""

from app.core.base import utcnow
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.user import User, UserStatus

# KAN-200 (Users → Lifecycle: `Deactivated → Active`, reactivation allowed):
# a deactivated user grants nothing and changes no flag while deactivated,
# but the status itself is not terminal any more — PATCH `status: active`
# brings them back (audited; the lifecycle stamps clear). Nor is a
# terminated employment: it ends with the user and returns with them.
_TERMINAL_USER_STATUSES = {UserStatus.DEACTIVATED}
_ACTIVE_USER_STATUSES = {UserStatus.INVITED, UserStatus.ACTIVE}


def is_active_user(user: User) -> bool:
    return user.status in _ACTIVE_USER_STATUSES


def membership_is_active():
    """KAN-199: a membership counts only while it has no end date, or one
    still in the future — an ended sister-Betrieb access keeps its row."""

    now = utcnow()
    return (DealershipMembership.valid_to.is_(None)) | (DealershipMembership.valid_to > now)
