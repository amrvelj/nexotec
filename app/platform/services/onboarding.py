"""Onboarding a Betrieb (KAN-210; Dealer Administration D-A-06, FR-A-24/25).

One platform_admin request, one transaction: the group (existing, or a new
group of one), the legal entity with its UID — unique across Nexotec, the
database's `uq_dealership_uid` backing the check — the module entitlements
(D-A-04; none, a service-only garage, is valid, D-A-10), the first manager
(a user with the manager flag, `invited`, and the invitation token), the
seeds the create path already lays down, and one `platform.dealership.onboarded`
outbox row. A failure anywhere leaves nothing behind.

What a new Betrieb gets, and where it comes from:
- its first location from the seat address, its number ranges with the
  neutral seeds and its fiscal defaults — `dealership.create_dealership`;
- the four-language standard texts, the document template (no logo) and the
  platform reference data — read from the platform's seeds and defaults
  until the Betrieb adapts them (KAN-208, WP-6b: no per-Betrieb rows needed);
- MFA enforced — `dealership_auth_config` reads as enforced while it has no
  row (FR-AU-11);
- `status = pending_onboarding` (the model's default; the activation gate is
  KAN-214).
- Role presets: none to seed — permissions are platform-defined (D-A-03).

Nexotec sets nothing commercial (D-A-06): no VAT rate, no bank account, the
neutral payment terms of `dealership_settings` only.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import AccessRole
from app.core.errors import ConflictError
from app.core.outbox import OutboxEvent, publish
from app.platform.models.dealership import Dealership, Location
from app.platform.models.user import User
from app.platform.reference_data_seed import LEGAL_FORM_LIST_CODE
from app.platform.schemas.dealership import DealershipCreate
from app.platform.schemas.onboarding import OnboardingRequest
from app.platform.schemas.user import UserCreate
from app.platform.services import dealership as dealership_service
from app.platform.services import entitlement as entitlement_service
from app.platform.services import identity
from app.platform.services import user as user_service
from app.platform.services.mailer import PlatformMailer

EVENT_TYPE = "platform.dealership.onboarded"


@dataclass(frozen=True)
class Onboarded:
    dealership: Dealership
    manager: User
    location: Location
    entitlements: frozenset[str]
    invitation_delivered: bool


def _already_onboarded() -> ConflictError:
    # Names nothing about the other tenant: only that this UID is taken.
    return ConflictError(
        "A Betrieb with this UID is already onboarded.", details={"reason": "uid_already_onboarded"}
    )


def onboard(
    db: Session,
    *,
    data: OnboardingRequest,
    actor_id: uuid.UUID,
    mailer: PlatformMailer,
    ip_address: str,
) -> Onboarded:
    legal = data.legal_entity
    if db.scalar(select(Dealership.id).where(Dealership.uid == legal.uid)) is not None:
        raise _already_onboarded()
    if data.group.dealer_group_id is not None:
        dealership_service.get_dealer_group_or_404(db, data.group.dealer_group_id)
    if legal.legal_form is not None:
        dealership_service._assert_reference_codes(
            db, list_code=LEGAL_FORM_LIST_CODE, label="legalForm", codes=[legal.legal_form]
        )

    try:
        dealership = dealership_service.create_dealership(
            db,
            data=DealershipCreate(
                dealer_group_id=data.group.dealer_group_id,
                legal_name=legal.legal_name,
                trade_name=legal.trade_name,
                franchise_type=legal.franchise_type,
                address=legal.address,
                phone=legal.phone,
                uid=legal.uid,
            ),
            actor_id=actor_id,
            commit=False,
        )
    except ConflictError as exc:
        # The unique index refused a racing onboarding of the same UID.
        raise _already_onboarded() from exc
    if data.group.new_group_name is not None:
        group = dealership_service.get_dealer_group_or_404(db, dealership.dealer_group_id)
        group.name = data.group.new_group_name
    if legal.legal_form is not None:
        dealership.legal_form = legal.legal_form

    entitlements = entitlement_service.set_entitlements(
        db, dealership=dealership, modules=set(data.entitlements), actor_id=actor_id, commit=False
    )
    location = db.scalars(select(Location).where(Location.tenant_id == dealership.id)).one()
    manager = user_service.create_user(
        db,
        dealership_id=dealership.id,
        data=UserCreate(
            first_name=data.manager.first_name,
            last_name=data.manager.last_name,
            email=data.manager.email,
            role=data.manager.role,
            access_roles=data.manager.access_roles,
            is_dealer_manager=True,
            primary_location_id=location.id,
        ),
        actor_id=actor_id,
        actor_roles=frozenset({AccessRole.PLATFORM_ADMIN}),
        commit=False,
    )
    publish(
        db,
        OutboxEvent(
            event_type=EVENT_TYPE,
            tenant_id=dealership.id,
            producer="platform",
            aggregate_type="dealership",
            aggregate_id=dealership.id,
            payload={
                "dealershipId": str(dealership.id),
                "dealerGroupId": str(dealership.dealer_group_id),
                "legalName": dealership.legal_name,
                "entitlements": sorted(entitlements),
                "managerUserId": str(manager.id),
                "onboardedBy": str(actor_id),
            },
        ),
    )
    # Last before the commit: the invitation mail names a token this
    # transaction writes (the create-user route's order, FR-A-09).
    delivered = identity.issue_invitation(db, mailer, user=manager, actor_id=actor_id, ip_address=ip_address)
    db.commit()
    db.refresh(dealership)
    db.refresh(manager)
    return Onboarded(
        dealership=dealership,
        manager=manager,
        location=location,
        entitlements=entitlements,
        invitation_delivered=delivered,
    )
