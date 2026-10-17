"""Platform's nightly reconciliation (KAN-318). Platform has no outbound
cross-context reference to check: user.tenant_id -> dealership.id and
dealership.dealer_group_id -> dealer_group.id are its own. What it does
have is the same created_by / updated_by columns as every other context,
and KAN-243 checks those everywhere — so this job carries exactly the
actor checks, built by app.platform.services.actor_reference as the other
contexts build theirs through app.platform.public. Everything here is
read-only — see app.core.reconciliation for the mechanism.
"""

from sqlalchemy.orm import Session

from app.core.reconciliation import ReconciliationRun, ReferenceCheck, StateCheck, run_reconciliation
from app.platform.models.dealership import DealerGroup, Dealership, Location
from app.platform.models.document_template import DocumentTemplate
from app.platform.models.module_entitlement import ModuleEntitlement
from app.platform.models.reference_data import ReferenceList, ReferenceValue
from app.platform.models.user import User
from app.platform.services.actor_reference import actor_reference_checks

CONTEXT = "platform"

# Every platform table with created_by / updated_by (or granted_by): each must name a
# platform user, or be null (KAN-243; see
# app.platform.services.actor_reference). The user table is in the list too:
# a user's own actor columns name whoever created it, which may be itself
# (the first user of a fresh environment, scripts/seed_staging_demo.py).
ACTOR_TABLES = (
    DealerGroup,
    Dealership,
    Location,
    User,
    ReferenceList,
    ReferenceValue,
    DocumentTemplate,
    ModuleEntitlement,  # granted_by (KAN-198): an actor column under another name
)

CHECKS: list[ReferenceCheck | StateCheck] = [*actor_reference_checks(*ACTOR_TABLES)]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)
