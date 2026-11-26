"""Platform's nightly reconciliation (KAN-318). Platform has no outbound
cross-context reference to check: user.tenant_id -> dealership.id and
dealership.dealer_group_id -> dealer_group.id are its own. What it does
have is the same created_by / updated_by columns as every other context,
and KAN-243 checks those everywhere — so this job carries exactly the
actor checks, built by app.platform.identity.services.actor_reference as the other
contexts build theirs through app.platform.public — and, since KAN-412, one
StateCheck of its own: a revoked connected account whose secret is still
held a day after the revoke; since KAN-218 a second: an offboarded Betrieb
whose ten-year archive has passed its last day, due for manual deletion;
since KAN-764 a platform_admin homed outside the platform tenant.
Everything here is read-only — see app.core.reconciliation for the mechanism.
"""

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, String, and_, cast, false, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.contact.public import contact_exists, contact_in_group, contact_is_merged, contact_reference_check
from app.core.auth import AccessRole
from app.core.base import utcnow
from app.core.config import get_settings
from app.core.reconciliation import ReconciliationRun, ReferenceCheck, StateCheck, run_reconciliation
from app.platform.administration.models.export_job import ExportJob, ExportJobStatus
from app.platform.administration.models.status_page import PlatformStatusIncident
from app.platform.documents.models.document_link import DocumentLink
from app.platform.documents.models.document_template import DocumentTemplate
from app.platform.documents.models.standard_text import StandardText
from app.platform.identity.models.auth_event import AuthEvent
from app.platform.identity.models.auth_token import AuthToken
from app.platform.identity.models.connected_account import ConnectedAccountStatus, UserConnectedAccount
from app.platform.identity.models.credential import Credential
from app.platform.identity.models.dealership_auth_config import DealershipAuthConfig
from app.platform.identity.models.dealership_membership import DealershipMembership
from app.platform.identity.models.standard_text_delegation import StandardTextDelegation
from app.platform.identity.models.support_session import SupportSession
from app.platform.identity.models.user import User
from app.platform.identity.models.user_labour_category import UserLabourCategory
from app.platform.identity.services.actor_reference import actor_reference_checks
from app.platform.organisation.models.branding_asset import BrandingAsset
from app.platform.organisation.models.contact_group import DealershipContactGroup
from app.platform.organisation.models.dealership import (
    DealerGroup,
    Dealership,
    DealershipKind,
    DealershipStatus,
    Location,
)
from app.platform.organisation.models.dealership_bank_account import DealershipBankAccount
from app.platform.organisation.models.dealership_settings import DealershipSettings
from app.platform.organisation.models.holiday_calendar import Holiday, HolidayCalendar
from app.platform.organisation.models.labour_rate import LabourRate, LabourRateAgreement, LabourRateCategory
from app.platform.organisation.models.location_schedule import LocationClosure
from app.platform.organisation.models.module_entitlement import ModuleEntitlement
from app.platform.organisation.models.number_range import NumberRange
from app.platform.organisation.models.publishing_profile import LocationLeadRouting, PublishingProfile
from app.platform.organisation.models.setting_value import SettingState, SettingValue, SettingValueChange
from app.platform.organisation.services.labour_rate_settings import TIME_UNIT_KEY, WORK_UNITS, WORK_UNITS_PER_HOUR_KEY
from app.platform.organisation.services.reconciliation_targets import dealership_group_of
from app.platform.organisation.services.setting_declarations import multiple_rules
from app.platform.reference.models.reference_data import ReferenceList, ReferenceValue
from app.platform.reference.models.ui_translation import UiTranslation

CONTEXT = "platform"

# Every platform table with created_by / updated_by (or granted_by): each must name a
# platform user, or be null (KAN-243; see
# app.platform.identity.services.actor_reference). The user table is in the list too:
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
    PublishingProfile,  # created_by / updated_by (KAN-225)
    LocationLeadRouting,  # created_by / updated_by (KAN-225)
    NumberRange,  # created_by / updated_by (KAN-207; seeded rows carry none)
    DealershipContactGroup,  # created_by / updated_by: who switched it on (KAN-466)
    ExportJob,  # created_by / updated_by: who requested the export (KAN-218)
    PlatformStatusIncident,  # created_by / updated_by: the staff user who opened or edited it (KAN-223)
    SupportSession,  # created_by / updated_by: the staff member or the user who ended it (KAN-221)
    DocumentLink,  # created_by / updated_by: the user whose send issued the link (KAN-217)
    SettingValue,  # created_by / updated_by: the manager who wrote the slot (KAN-805)
    SettingValueChange,  # created_by: who made the change (KAN-805)
    LabourRateCategory,  # created_by / updated_by (KAN-806)
    LabourRate,  # created_by / updated_by: who typed it, who withdrew it (KAN-806)
    LabourRateAgreement,  # created_by / updated_by (KAN-806)
    UserLabourCategory,  # created_by / updated_by: who allocated the user (KAN-806)
)

# KAN-412 (FR-AU-24, rule 10): a revoked grant's provider end is recorded by
# dropping its secret_ref (app.platform.identity.services.connected_account.
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


# KAN-738 (ADR-185): the web service's export runner produces a requested
# export within seconds and deletes a file within a minute of its seven days
# (Anto's Q1 A). An hour later either is a runner that stopped or keeps failing
# — what the outbox's dead letter and the daily job's alarm said before.
_EXPORT_RUNNER_GRACE = dt.timedelta(hours=1)


def _export_requested_not_produced() -> ColumnElement[bool]:
    return and_(ExportJob.status == ExportJobStatus.REQUESTED, ExportJob.created_at < utcnow() - _EXPORT_RUNNER_GRACE)


def _export_file_past_its_download_window() -> ColumnElement[bool]:
    return and_(
        ExportJob.status == ExportJobStatus.READY,
        ExportJob.download_expires_at < utcnow() - _EXPORT_RUNNER_GRACE,
    )


# KAN-764 (rule 10; KAN-221 PR-1): platform_admin is Nexotec staff and is
# granted only in the platform tenant. A holder homed anywhere else — a legacy
# row `scripts/move_platform_admins_to_platform_tenant.py` has not moved yet,
# or one a direct database fix wrote — is reported every night, never moved
# here: the move script's confirmed-rows rule decides each row (ADR-107). A
# `tenant_id` that names no Betrieb at all is reported too: it is not the
# platform tenant either. `access_roles` is a JSON list of fixed role tokens,
# so the quoted token in its text is the portable containment test (JSON,
# not JSONB, has no `@>`).
def _platform_admin_homed_outside_the_platform_tenant() -> ColumnElement[bool]:
    platform_tenants = select(Dealership.id).where(Dealership.kind == DealershipKind.PLATFORM)
    return and_(
        cast(User.access_roles, String).like(f'%"{AccessRole.PLATFORM_ADMIN.value}"%'),
        User.tenant_id.not_in(platform_tenants),
    )



# KAN-806: an agreement whose contact was merged keeps the duplicate's id until the
# consumer platform.labour_rate_agreements_on_contact_merged (KAN-831) re-points it;
# reported a day after the merged contact's last write. A row it set aside (the
# survivor's own row of that date won) stays on the duplicate by design.
_AGREEMENT_MERGE_GRACE = dt.timedelta(days=1)


def _agreement_names_a_merged_contact() -> ColumnElement[bool]:
    return LabourRateAgreement.withdrawn_at.is_(None) & LabourRateAgreement.set_aside_at.is_(None) & contact_is_merged(
        LabourRateAgreement.contact_id, merged_before=utcnow() - _AGREEMENT_MERGE_GRACE
    ).correlate(LabourRateAgreement)


def _agreement_contact_outside_group() -> ColumnElement[bool]:
    group = dealership_group_of(LabourRateAgreement.dealership_id, correlate=LabourRateAgreement)
    named = contact_exists(LabourRateAgreement.contact_id).correlate(LabourRateAgreement)
    return named & ~contact_in_group(LabourRateAgreement.contact_id, group).correlate(LabourRateAgreement)


def _work_units_without_units_per_hour() -> ColumnElement[bool]:
    """KAN-806 (check 49): an O18 slot on «work units» where no O19 is in
    force — the slot's own location's, else the Betrieb's. Writes refuse it
    (`setting_rules`), but a location deactivated while it relied on the
    Betrieb's O19, which was then cleared, comes back so when reactivated;
    `labour_time_unit` then answers hours. Only active locations count."""

    per_hour = aliased(SettingValue)
    own_location = (
        select(per_hour.id)
        .where(
            per_hour.dealership_id == SettingValue.dealership_id,
            per_hour.key == WORK_UNITS_PER_HOUR_KEY,
            per_hour.state == SettingState.SET,
            per_hour.location_id == SettingValue.location_id,
        )
        .exists()
    )
    betriebs = (
        select(per_hour.id)
        .where(
            per_hour.dealership_id == SettingValue.dealership_id,
            per_hour.key == WORK_UNITS_PER_HOUR_KEY,
            per_hour.state == SettingState.SET,
            per_hour.location_id.is_(None),
        )
        .exists()
    )
    location_active = select(Location.id).where(Location.id == SettingValue.location_id, Location.is_active.is_(True)).exists()
    return (
        (SettingValue.key == TIME_UNIT_KEY)
        & (SettingValue.state == SettingState.SET)
        & (SettingValue.value_choice == WORK_UNITS)
        & (SettingValue.location_id.is_(None) | location_active)
        & ~own_location.correlate(SettingValue)
        & ~betriebs.correlate(SettingValue)
    )


def _decimal_in_force(key: str, *, at_the_rows_location: bool) -> ColumnElement:
    """The value of `key` set in the row's Betrieb — at the row's own location,
    or the Betrieb's own — as a scalar subquery (NULL when not set)."""

    other = aliased(SettingValue)
    where = [other.dealership_id == SettingValue.dealership_id, other.key == key, other.state == SettingState.SET]
    where.append(other.location_id == SettingValue.location_id if at_the_rows_location else other.location_id.is_(None))
    return select(other.value_decimal).where(*where).correlate(SettingValue).scalar_subquery()


def _multiples_broken() -> ColumnElement[bool]:
    """KAN-808: a number declared `multiple_of` another (O7 of O6) that is no
    multiple of it in a scope where both are in force — the Betrieb, or an
    active location (its own value, else the Betrieb's). Writes refuse it
    (`setting_rules`), but a location deactivated while it relied on the
    Betrieb's values, which were then changed, comes back so when reactivated.

    Reported on the multiple's slot when the scope has one of its own (or is
    the Betrieb), else on the location's slot of the other key. Built from the
    registered declarations when the check runs."""

    location_active = (
        select(Location.id).where(Location.id == SettingValue.location_id, Location.is_active.is_(True)).exists()
    )
    conditions: list[ColumnElement[bool]] = []
    for multiple, of_key in multiple_rules():
        own_divisor = _decimal_in_force(of_key, at_the_rows_location=True)
        betriebs_divisor = _decimal_in_force(of_key, at_the_rows_location=False)
        divisor = func.coalesce(own_divisor, betriebs_divisor)
        conditions.append(
            (SettingValue.key == multiple.key)
            & (SettingValue.state == SettingState.SET)
            & (SettingValue.location_id.is_(None) | location_active)
            & (divisor > 0)
            & (SettingValue.value_decimal % divisor != 0)
        )
        own_multiple = _decimal_in_force(multiple.key, at_the_rows_location=True)
        betriebs_multiple = _decimal_in_force(multiple.key, at_the_rows_location=False)
        conditions.append(
            (SettingValue.key == of_key)
            & (SettingValue.state == SettingState.SET)
            & SettingValue.location_id.is_not(None)
            & location_active
            & own_multiple.is_(None)
            & (SettingValue.value_decimal > 0)
            & (betriebs_multiple % SettingValue.value_decimal != 0)
        )
    return or_(*conditions) if conditions else false()


def _rate_location_elsewhere() -> ColumnElement[bool]:
    return (
        select(Location.id)
        .where(Location.id == LabourRate.location_id, Location.tenant_id != LabourRate.dealership_id)
        .correlate(LabourRate)
        .exists()
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
    StateCheck(
        label="export_job.requested_an_hour_ago_and_not_produced",
        source_model=ExportJob,
        source_row_id_column=ExportJob.id,
        where=_export_requested_not_produced,
    ),
    StateCheck(
        label="export_job.file_kept_past_its_download_window",
        source_model=ExportJob,
        source_row_id_column=ExportJob.id,
        where=_export_file_past_its_download_window,
    ),
    StateCheck(
        label="user.platform_admin_homed_outside_the_platform_tenant",
        source_model=User,
        source_row_id_column=User.id,
        where=_platform_admin_homed_outside_the_platform_tenant,
    ),
    # KAN-806: an agreed labour rate names a contact, which contact owns (rule 2). It is
    # read here only, through contact.public; a merge's re-point and the label refresh are
    # a follow-up (the agreement keeps the label it was given until then).
    contact_reference_check(
        label="labour_rate_agreement.contact_id -> contact.id",
        source_model=LabourRateAgreement,
        source_row_id_column=LabourRateAgreement.id,
        source_fk_column=LabourRateAgreement.contact_id,
    ),
    StateCheck(
        label="labour_rate_agreement.contact_id names a merged contact",
        source_model=LabourRateAgreement,
        source_row_id_column=LabourRateAgreement.id,
        where=_agreement_names_a_merged_contact,
    ),
    StateCheck(
        label="labour_rate_agreement.contact_id names a contact outside the business's group",
        source_model=LabourRateAgreement,
        source_row_id_column=LabourRateAgreement.id,
        where=_agreement_contact_outside_group,
    ),
    StateCheck(
        label="setting_value labour_rates.time_unit on work units without work units per hour",
        source_model=SettingValue,
        source_row_id_column=SettingValue.id,
        where=_work_units_without_units_per_hour,
    ),
    StateCheck(
        label="setting_value a number declared a multiple of another setting that is no multiple of it",
        source_model=SettingValue,
        source_row_id_column=SettingValue.id,
        where=_multiples_broken,
    ),
    StateCheck(
        label="labour_rate.location_id names another business's location",
        source_model=LabourRate,
        source_row_id_column=LabourRate.id,
        where=_rate_location_elsewhere,
    ),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)
