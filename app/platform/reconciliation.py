"""Platform's nightly reconciliation (KAN-318). Platform has no outbound
cross-context reference to check: user.tenant_id -> dealership.id and
dealership.dealer_group_id -> dealer_group.id are its own. What it does
have is the same created_by / updated_by columns as every other context,
and KAN-243 checks those everywhere — so this job carries exactly the
actor checks, built by app.platform.services.actor_reference as the other
contexts build theirs through app.platform.public — and, since KAN-412, one
StateCheck of its own: a revoked connected account whose secret is still
held a day after the revoke; since KAN-218 a second: an offboarded Betrieb
whose ten-year archive has passed its last day, due for manual deletion.
Everything here is read-only — see app.core.reconciliation for the mechanism.
"""

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.config import get_settings
from app.core.reconciliation import ReconciliationRun, ReferenceCheck, StateCheck, run_reconciliation
from app.platform.models.auth_event import AuthEvent
from app.platform.models.auth_token import AuthToken
from app.platform.models.connected_account import ConnectedAccountStatus, UserConnectedAccount
from app.platform.models.credential import Credential
from app.platform.models.dealership_auth_config import DealershipAuthConfig
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.document_template import DocumentTemplate
from app.platform.models.export_job import ExportJob
from app.platform.models.standard_text import StandardText
from app.platform.models.standard_text_delegation import StandardTextDelegation
from app.platform.models.user import User
from app.platform.organisation.models.branding_asset import BrandingAsset
from app.platform.organisation.models.contact_group import DealershipContactGroup
from app.platform.organisation.models.dealership import DealerGroup, Dealership, DealershipStatus, Location
from app.platform.organisation.models.dealership_bank_account import DealershipBankAccount
from app.platform.organisation.models.dealership_settings import DealershipSettings
from app.platform.organisation.models.holiday_calendar import Holiday, HolidayCalendar
from app.platform.organisation.models.location_schedule import LocationClosure
from app.platform.organisation.models.module_entitlement import ModuleEntitlement
from app.platform.organisation.models.number_range import NumberRange
from app.platform.reference.models.reference_data import ReferenceList, ReferenceValue
from app.platform.reference.models.ui_translation import UiTranslation
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
    DealershipMembership,  # granted_by (KAN-199)
    StandardTextDelegation,  # granted_by (KAN-199)
    StandardText,  # created_by / updated_by (KAN-208)
    Credential,  # created_by / updated_by (KAN-196)
    DealershipAuthConfig,
    AuthToken,  # created_by: the issuer (KAN-196)
    AuthEvent,  # actor_id (KAN-196)
    UserConnectedAccount,  # created_by / updated_by / revoked_by (KAN-201)
    DealershipBankAccount,  # created_by / updated_by (KAN-203)
    DealershipSettings,  # created_by / updated_by (KAN-203)
    UiTranslation,  # created_by / updated_by: the platform admin who published (KAN-209)
    HolidayCalendar,  # created_by / updated_by (KAN-204; seeded rows carry none)
    Holiday,
    LocationClosure,  # created_by / updated_by (KAN-204)
    BrandingAsset,  # uploaded_by (KAN-205)
    NumberRange,  # created_by / updated_by (KAN-207; seeded rows carry none)
    DealershipContactGroup,  # created_by / updated_by: who switched it on (KAN-466)
    ExportJob,  # created_by / updated_by: who requested the export (KAN-218)
)

# KAN-412 (FR-AU-24, rule 10): a revoked grant's provider end is recorded by
# dropping its secret_ref (app.platform.services.connected_account.
# end_at_provider). One still holding a reference a day after the revoke
# means the provider's end never happened: the revoker kept refusing (its
# event dead-lettered), or no revoker is registered for the provider — the
# grant may still be live at the provider and its tokens are still in the
# secrets manager. Reported for every provider, registered revoker or not:
# a missing registration is exactly the failure this check exists to see,
# and it reads the same at night whether or not the worker registered one.
# The day is far beyond the outbox's retry budget, so a finding is never a
# delivery still under way.
REVOKED_GRANT_GRACE = dt.timedelta(hours=24)


def _revoked_grant_secret_still_held() -> ColumnElement[bool]:
    return and_(
        UserConnectedAccount.status == ConnectedAccountStatus.REVOKED,
        UserConnectedAccount.secret_ref.is_not(None),
        UserConnectedAccount.revoked_at < utcnow() - REVOKED_GRANT_GRACE,
    )


# KAN-218 (FR-A-45; Anto, 2026-11-14): nothing deletes an offboarded
# Betrieb's archive automatically. Its last day is `retention_until` (ten
# full years from the offboarding date); from the next Swiss calendar day on
# the Betrieb is reported every night as due for manual deletion — a staff
# step in an attended session — until that step is done. Reported, never
# repaired (ADR-107).
def _archive_due_for_manual_deletion() -> ColumnElement[bool]:
    today = dt.datetime.now(ZoneInfo(get_settings().session_timezone)).date()
    return and_(
        Dealership.status == DealershipStatus.OFFBOARDED,
        Dealership.retention_until.is_not(None),
        Dealership.retention_until < today,
    )


CHECKS: list[ReferenceCheck | StateCheck] = [
    *actor_reference_checks(*ACTOR_TABLES),
    StateCheck(
        label="user_connected_account.revoked_grant_secret_still_held",
        source_model=UserConnectedAccount,
        source_row_id_column=UserConnectedAccount.id,
        where=_revoked_grant_secret_still_held,
    ),
    StateCheck(
        label="dealership.archive_due_for_manual_deletion",
        source_model=Dealership,
        source_row_id_column=Dealership.id,
        where=_archive_due_for_manual_deletion,
    ),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)
