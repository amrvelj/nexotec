"""Platform's export declaration (KAN-218, FR-A-44): the Betrieb's own
master data, its locations, users and settings, and its uploaded files
(logos, photos, signatures — Anto, 2026-11-13, Q4 A).
"""

from collections.abc import Iterable, Mapping, Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.export import ExportedTable, ExportFile, ExportScope, TableExporter, table_of
from app.core.object_store import ObjectStore, get_object_store
from app.platform.models.auth_event import AuthEvent
from app.platform.models.auth_session import AuthSession
from app.platform.models.auth_token import AuthToken
from app.platform.models.credential import Credential
from app.platform.models.dealership_auth_config import DealershipAuthConfig
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.document_template import DocumentTemplate
from app.platform.models.export_job import ExportJob
from app.platform.models.legal import LegalAcceptance, LegalDocument
from app.platform.models.mfa import MfaPasskey, MfaRecoveryCode, MfaTotp
from app.platform.models.notification import (
    Notification,
    NotificationEvent,
    PushSubscription,
    UserNotificationPreference,
)
from app.platform.models.setup_checklist import SetupChecklistRegression
from app.platform.models.standard_text import StandardText
from app.platform.models.standard_text_delegation import StandardTextDelegation
from app.platform.models.user import User
from app.platform.models.user_location import UserLocation
from app.platform.models.user_preference import UserPreference
from app.platform.organisation.models.branding_asset import IMAGERY_KINDS, BrandingAsset
from app.platform.organisation.models.dealership import DealerGroup, Dealership, Location
from app.platform.organisation.models.dealership_bank_account import DealershipBankAccount
from app.platform.organisation.models.dealership_settings import DealershipSettings
from app.platform.organisation.models.holiday_calendar import Holiday, HolidayCalendar
from app.platform.organisation.models.location_schedule import LocationClosure, LocationOpeningHours
from app.platform.organisation.models.module_entitlement import ModuleEntitlement
from app.platform.organisation.models.number_range import NumberRange, NumberRangeConfirmation
from app.platform.organisation.models.publishing_profile import LocationLeadRouting, PublishingProfile
from app.platform.organisation.services import contact_group
from app.platform.reference.models.reference_data import ReferenceList, ReferenceValue
from app.platform.reference.models.ui_translation import MissingTranslation, UiTranslation, UiTranslationRevision
from app.platform.services import connected_account

_user = table_of(User)
_location = table_of(Location)


def _home_user_ids(scope: ExportScope):
    return select(_user.c.id).where(_user.c.tenant_id == scope.dealership_id)


def _by_dealership(table, column: str = "dealership_id"):
    return lambda scope: table.c[column] == scope.dealership_id


def _by_home_user(table):
    return lambda scope: table.c.user_id.in_(_home_user_ids(scope))


def _by_location(table):
    return lambda scope: table.c.location_id.in_(
        select(_location.c.id).where(_location.c.tenant_id == scope.dealership_id)
    )


def _home_user_or_own_location(table):
    return lambda scope: or_(_by_home_user(table)(scope), _by_location(table)(scope))


def _dealership_or_home_user(table):
    return lambda scope: or_(table.c.dealership_id == scope.dealership_id, table.c.user_id.in_(_home_user_ids(scope)))


def _secret(table, reason: str) -> ExportedTable:
    """The Betrieb's rows, never written into the archive."""

    return ExportedTable(
        table=table, scope=_by_home_user(table), in_archive=False, exclusion_reason=reason, marks_write=False
    )


class PlatformExporter(TableExporter):
    context = "platform"

    def __init__(self, store: ObjectStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> ObjectStore:
        return self._store or get_object_store()

    def exported_tables(self) -> Sequence[ExportedTable]:
        dealership = table_of(Dealership)
        group = table_of(DealerGroup)
        session = table_of(AuthSession)
        return (
            ExportedTable(
                table=dealership,
                scope=lambda scope: dealership.c.id == scope.dealership_id,
            ),
            ExportedTable(
                table=group,
                scope=lambda scope: group.c.id == scope.group_id,
                marks_write=False,
            ),
            ExportedTable(table=_location, scope=_by_dealership(_location, "tenant_id")),
            ExportedTable(table=table_of(LocationOpeningHours), scope=_by_location(table_of(LocationOpeningHours))),
            ExportedTable(table=table_of(LocationClosure), scope=_by_location(table_of(LocationClosure))),
            ExportedTable(table=table_of(DealershipSettings), scope=_by_dealership(table_of(DealershipSettings))),
            ExportedTable(table=table_of(DealershipAuthConfig), scope=_by_dealership(table_of(DealershipAuthConfig))),
            ExportedTable(table=table_of(DealershipBankAccount), scope=_by_dealership(table_of(DealershipBankAccount))),
            contact_group.export_table(),
            ExportedTable(table=table_of(DocumentTemplate), scope=_by_dealership(table_of(DocumentTemplate))),
            ExportedTable(table=table_of(BrandingAsset), scope=_by_dealership(table_of(BrandingAsset))),
            ExportedTable(table=table_of(PublishingProfile), scope=_by_dealership(table_of(PublishingProfile))),
            ExportedTable(table=table_of(LocationLeadRouting), scope=_by_dealership(table_of(LocationLeadRouting))),
            ExportedTable(table=table_of(ModuleEntitlement), scope=_by_dealership(table_of(ModuleEntitlement))),
            ExportedTable(table=table_of(NumberRange), scope=_by_dealership(table_of(NumberRange), "tenant_id")),
            # KAN-214: the manager's confirmations of the ranges and the Betrieb's
            # acceptances of the ToS / DPA — its own records (the legal basis, A-1).
            ExportedTable(
                table=table_of(NumberRangeConfirmation), scope=_by_dealership(table_of(NumberRangeConfirmation))
            ),
            ExportedTable(table=table_of(LegalAcceptance), scope=_by_dealership(table_of(LegalAcceptance))),
            ExportedTable(
                table=table_of(SetupChecklistRegression),
                scope=_by_dealership(table_of(SetupChecklistRegression)),
                in_archive=False,
                exclusion_reason="The daily job's notification ledger; the checklist itself is derived.",
                marks_write=False,
            ),
            ExportedTable(table=table_of(StandardText), scope=_by_dealership(table_of(StandardText))),
            ExportedTable(
                table=table_of(StandardTextDelegation), scope=_by_dealership(table_of(StandardTextDelegation))
            ),
            ExportedTable(
                table=_user,
                scope=_by_dealership(_user, "tenant_id"),
                # A sign-in stamps the row; a manager's edit of a user is audited.
                marks_write=False,
            ),
            # The Betrieb's users' sites, and anyone's assignment to one of its sites.
            ExportedTable(table=table_of(UserLocation), scope=_home_user_or_own_location(table_of(UserLocation))),
            ExportedTable(
                table=table_of(DealershipMembership), scope=_dealership_or_home_user(table_of(DealershipMembership))
            ),
            ExportedTable(
                table=table_of(UserPreference), scope=_by_home_user(table_of(UserPreference)), marks_write=False
            ),
            ExportedTable(
                table=table_of(UserNotificationPreference),
                scope=_by_home_user(table_of(UserNotificationPreference)),
                marks_write=False,
            ),
            ExportedTable(
                table=table_of(Notification), scope=_by_dealership(table_of(Notification)), marks_write=False
            ),
            ExportedTable(table=table_of(AuthEvent), scope=_by_dealership(table_of(AuthEvent)), marks_write=False),
            # The Betrieb's, never in the archive.
            ExportedTable(
                table=session,
                scope=_dealership_or_home_user(session),
                in_archive=False,
                exclusion_reason="Sign-in state; holds no record of its own.",
                marks_write=False,
            ),
            _secret(table_of(Credential), "The password hash — a secret, never returned."),
            _secret(table_of(AuthToken), "Single-use invitation and reset tokens, stored hashed — secrets."),
            _secret(table_of(MfaTotp), "The TOTP seed — the one secret in the database (ADR-099)."),
            _secret(table_of(MfaRecoveryCode), "Hashed recovery codes — secrets."),
            _secret(table_of(MfaPasskey), "Passkey public keys — authentication material, not a record."),
            _secret(table_of(PushSubscription), "A browser's push endpoint and keys — device material."),
            connected_account.export_table(),
            ExportedTable(
                table=table_of(ExportJob),
                scope=_by_dealership(table_of(ExportJob)),
                in_archive=False,
                exclusion_reason="The export's own bookkeeping; its files are the exports themselves.",
                marks_write=False,
            ),
        )

    def excluded_tables(self) -> Mapping[str, str]:
        platform_reference = "Nexotec's platform reference data, the same for every Betrieb."
        return {
            HolidayCalendar.__tablename__: platform_reference,
            Holiday.__tablename__: platform_reference,
            ReferenceList.__tablename__: platform_reference,
            ReferenceValue.__tablename__: platform_reference,
            NotificationEvent.__tablename__: "Nexotec's catalogue of notification kinds.",
            LegalDocument.__tablename__: "Nexotec's own Terms of Service and DPA, the same for every Betrieb.",
            UiTranslation.__tablename__: "Nexotec's interface texts.",
            UiTranslationRevision.__tablename__: "Nexotec's interface texts.",
            MissingTranslation.__tablename__: "Nexotec's interface texts.",
        }

    def files(self, db: Session, scope: ExportScope) -> Iterable[ExportFile]:
        store = self.store
        # KAN-206: one row per kind for the Betrieb and one per kind for each
        # location — a site's logo leaves under its location, never over the
        # Betrieb's own.
        assets = db.scalars(
            select(BrandingAsset).where(BrandingAsset.dealership_id == scope.dealership_id).order_by(BrandingAsset.id)
        )
        for asset in assets:
            stored = store.get(asset.object_key)
            if stored is None:
                continue
            if asset.kind in IMAGERY_KINDS:
                # KAN-225: several photos per site and kind, so each by its id.
                path = f"files/publishing/locations/{asset.location_id}/{asset.kind.value}/{asset.id}"
            elif asset.location_id is None:
                path = f"files/branding/{asset.kind.value}"
            else:
                path = f"files/branding/locations/{asset.location_id}/{asset.kind.value}"
            yield ExportFile(path=path, content_type=stored.content_type, data=stored.data)
        profile = db.scalar(
            select(PublishingProfile).where(
                PublishingProfile.dealership_id == scope.dealership_id, PublishingProfile.location_id.is_(None)
            )
        )
        if profile is not None and profile.agb_object_key is not None:
            stored = store.get(profile.agb_object_key)
            if stored is not None:
                yield ExportFile(path="files/publishing/agb.pdf", content_type="application/pdf", data=stored.data)
        for user in db.scalars(select(User).where(User.tenant_id == scope.dealership_id).order_by(User.id)):
            for kind, key in (("photo", user.photo_ref), ("signature", user.signature_image_ref)):
                if key is None:
                    continue
                stored = store.get(key)
                if stored is not None:
                    yield ExportFile(
                        path=f"files/users/{user.id}/{kind}", content_type=stored.content_type, data=stored.data
                    )
