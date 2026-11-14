"""Platform's export declaration (KAN-218, FR-A-44): the Betrieb's own
master data, its locations, users and settings, and its uploaded files
(logos, photos, signatures — Anto, 2026-11-13, Q4 A).

At the deletion after the retention period (Q3 A) the Betrieb's rows go,
except three kept on purpose: the `dealership` row (the tombstone the users
and the platform audit row name — its identity, addresses, contact, fiscal
and licence columns blanked, since a sole proprietor's legal name is personal
data), the `user` rows (A-RULE-5: never deleted — their personal fields are
minimised and their files deleted, the identity row and its id stay) and the
dealer group (it owns nothing and may hold sisters; a group the Betrieb was
alone in loses its name and point of contact). Platform runs last, after
every other context (`services/offboarding.py`).
"""

import logging
from collections.abc import Iterable, Mapping, Sequence

from sqlalchemy import String, cast, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.export import ExportedTable, ExportFile, ExportScope, TableExporter, table_of
from app.core.object_store import ObjectStore, get_object_store
from app.platform.models.auth_event import AuthEvent
from app.platform.models.auth_session import AuthSession
from app.platform.models.auth_token import AuthToken
from app.platform.models.branding_asset import BrandingAsset
from app.platform.models.credential import Credential
from app.platform.models.dealership import DealerGroup, Dealership, Location
from app.platform.models.dealership_auth_config import DealershipAuthConfig
from app.platform.models.dealership_bank_account import DealershipBankAccount
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.dealership_settings import DealershipSettings
from app.platform.models.document_template import DocumentTemplate
from app.platform.models.export_job import ExportJob
from app.platform.models.holiday_calendar import Holiday, HolidayCalendar
from app.platform.models.location_schedule import LocationClosure, LocationOpeningHours
from app.platform.models.mfa import MfaPasskey, MfaRecoveryCode, MfaTotp
from app.platform.models.module_entitlement import ModuleEntitlement
from app.platform.models.notification import (
    Notification,
    NotificationEvent,
    PushSubscription,
    UserNotificationPreference,
)
from app.platform.models.number_range import NumberRange
from app.platform.models.reference_data import ReferenceList, ReferenceValue
from app.platform.models.standard_text import StandardText
from app.platform.models.standard_text_delegation import StandardTextDelegation
from app.platform.models.ui_translation import MissingTranslation, UiTranslation, UiTranslationRevision
from app.platform.models.user import User
from app.platform.models.user_location import UserLocation
from app.platform.models.user_preference import UserPreference
from app.platform.services import connected_account, contact_group

logger = logging.getLogger(__name__)

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
    """Deleted with the Betrieb, never written into the archive."""

    return ExportedTable(
        table=table, scope=_by_home_user(table), in_archive=False, exclusion_reason=reason, marks_write=False
    )


# What a minimised user row keeps (A-RULE-5): its id, Betrieb, status and the
# stamps — the trail every document and audit row names.
_ERASED_EMAIL_DOMAIN = "erased.invalid"

_ADDRESS_PARTS = ("street", "house_number", "postal_code", "locality", "canton", "country")

# What the erased Betrieb's row is left with: its id, group, status, the
# archive's dates (offboarded_at, retention_until, erasure_progress, erased_at),
# the created/updated stamps, the version, and its configuration (franchise
# type, brand colours, languages, VAT rate, entitlements — no one's identity).
# Nullable columns become NULL. The NOT NULL ones take a neutral placeholder
# that still fits DealershipRead, so staff can read the tombstone: "" for the
# legal name, street, house number, locality and phone; "0000" for the postal
# code (four digits, as a Swiss seat must have, and no Swiss postal code). The
# seat's canton and country stay: the read shape needs a real canton code, and
# a canton alone identifies no one. vat_liable stays too (a flag, no identity).
_ERASED_DEALERSHIP = {
    "legal_name": "",
    "trade_name": None,
    "oem_affiliations": None,
    "address_street": "",
    "address_house_number": "",
    "address_postal_code": "0000",
    "address_locality": "",
    **{f"{kind}_address_{part}": None for kind in ("correspondence", "billing") for part in _ADDRESS_PARTS},
    "phone": "",
    "uid": None,
    "vat_number": None,
    "legal_form": None,
    "commercial_register_office": None,
    "hr_number": None,
    "astra_operator_number": None,
    "collective_vehicle_licences": None,
    "association_memberships": None,
    "logo_url": None,
}

# A group the erased Betrieb was alone in: its point of contact goes, and its
# name too — a group of one is named after the Betrieb's legal name at
# onboarding (services/dealership.py). `name` is NOT NULL, so "".
_ERASED_SOLE_GROUP = {"name": "", "contact_name": None, "contact_email": None, "contact_phone": None}


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
                erase=False,
                keep_reason=(
                    "The tombstone the users (A-RULE-5) and the platform audit row of the deletion name; "
                    "its identity, address, contact, fiscal and licence columns are blanked."
                ),
            ),
            ExportedTable(
                table=group,
                scope=lambda scope: group.c.id == scope.group_id,
                erase=False,
                keep_reason="The dealer group owns nothing (ADR-014) and may hold sister Betriebe.",
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
            ExportedTable(table=table_of(ModuleEntitlement), scope=_by_dealership(table_of(ModuleEntitlement))),
            ExportedTable(table=table_of(NumberRange), scope=_by_dealership(table_of(NumberRange), "tenant_id")),
            ExportedTable(table=table_of(StandardText), scope=_by_dealership(table_of(StandardText))),
            ExportedTable(
                table=table_of(StandardTextDelegation), scope=_by_dealership(table_of(StandardTextDelegation))
            ),
            ExportedTable(
                table=_user,
                scope=_by_dealership(_user, "tenant_id"),
                erase=False,
                keep_reason="A-RULE-5: users are never deleted; personal fields are minimised, the row and id stay.",
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
            # Deleted with the Betrieb, never in the archive.
            ExportedTable(
                table=session,
                scope=_dealership_or_home_user(session),
                in_archive=False,
                exclusion_reason="Sign-in state; ends with the Betrieb and holds no record of its own.",
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
            UiTranslation.__tablename__: "Nexotec's interface texts.",
            UiTranslationRevision.__tablename__: "Nexotec's interface texts.",
            MissingTranslation.__tablename__: "Nexotec's interface texts.",
        }

    def files(self, db: Session, scope: ExportScope) -> Iterable[ExportFile]:
        store = self.store
        for asset in db.scalars(select(BrandingAsset).where(BrandingAsset.dealership_id == scope.dealership_id)):
            stored = store.get(asset.object_key)
            if stored is not None:
                yield ExportFile(
                    path=f"files/branding/{asset.kind.value}", content_type=stored.content_type, data=stored.data
                )
        for user in db.scalars(select(User).where(User.tenant_id == scope.dealership_id).order_by(User.id)):
            for kind, key in (("photo", user.photo_ref), ("signature", user.signature_image_ref)):
                if key is None:
                    continue
                stored = store.get(key)
                if stored is not None:
                    yield ExportFile(
                        path=f"files/users/{user.id}/{kind}", content_type=stored.content_type, data=stored.data
                    )

    def before_erase(self, db: Session, scope: ExportScope) -> int:
        """The stored files go first (logos, photos, signatures, the export
        ZIPs), then the users are minimised (A-RULE-5), the Betrieb's row is
        reduced to its tombstone and, when it was alone in its group, the
        group's name and contact are blanked. A key the store no longer holds
        is no error, so a rerun passes."""

        store = self.store
        keys = list(
            db.scalars(select(BrandingAsset.object_key).where(BrandingAsset.dealership_id == scope.dealership_id))
        )
        keys += [
            key
            for key in db.scalars(select(ExportJob.object_key).where(ExportJob.dealership_id == scope.dealership_id))
            if key
        ]
        users = list(db.scalars(select(User).where(User.tenant_id == scope.dealership_id)))
        keys += [key for user in users for key in (user.photo_ref, user.signature_image_ref) if key]
        for key in keys:
            store.delete(key)
        result = db.execute(
            update(User)
            .where(User.tenant_id == scope.dealership_id)
            .values(
                first_name="",
                last_name="",
                email=func.concat(cast(_user.c.id, String), f"@{_ERASED_EMAIL_DOMAIN}"),
                phone=None,
                direct_dial=None,
                employee_number=None,
                department=None,
                sales_advisor_code=None,
                signature_image_ref=None,
                photo_ref=None,
                signature_block=None,
                deactivation_reason="dealership_erased",
            )
            .execution_options(synchronize_session=False)
        )
        db.execute(
            update(Dealership)
            .where(Dealership.id == scope.dealership_id)
            .values(**_ERASED_DEALERSHIP, version=Dealership.version + 1)
            .execution_options(synchronize_session=False)
        )
        if scope.sole_in_group:
            db.execute(
                update(DealerGroup)
                .where(DealerGroup.id == scope.group_id)
                .values(**_ERASED_SOLE_GROUP, version=DealerGroup.version + 1)
                .execution_options(synchronize_session=False)
            )
        return int(getattr(result, "rowcount", 0) or 0)
