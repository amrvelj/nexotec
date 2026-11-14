"""KAN-214 test support: a Betrieb being set up, and the steps that complete
its checklist — each written to the owner's own table, so a test can leave
exactly one item open."""

import datetime as dt
import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, create_access_token
from app.core.i18n import SwissLanguage
from app.core.permissions import ModuleCode
from app.platform.models.legal import LegalDocument, LegalDocumentType
from app.platform.models.user import User, UserStatus
from app.platform.organisation.models.branding_asset import BrandingAsset, BrandingAssetKind
from app.platform.organisation.models.dealership import Dealership, DealershipStatus, Location
from app.platform.organisation.models.dealership_bank_account import DealershipBankAccount, QrReferenceType
from app.platform.organisation.models.location_schedule import LocationDepartment, LocationOpeningHours
from app.platform.organisation.models.module_entitlement import ModuleEntitlement
from app.platform.organisation.models.number_range import NumberRangeConfirmation
from tests.identity_support import create_dealership, create_user
from tests.token_support import dealer_group_of

IBAN = "CH9300762011623852957"


def pending_dealership(client: TestClient, db: Session, *, modules: tuple[ModuleCode, ...] = (ModuleCode.SALES,)) -> uuid.UUID:
    """A Betrieb as onboarding leaves it: `pending_onboarding`, holding `modules`."""

    dealership_id = uuid.UUID(create_dealership(client))
    for module in modules:
        db.add(ModuleEntitlement(dealership_id=dealership_id, module=module))
    db.commit()
    dealership = db.get(Dealership, dealership_id)
    assert dealership is not None
    assert dealership.status == DealershipStatus.PENDING_ONBOARDING
    return dealership_id


def manager(client: TestClient, db: Session, dealership_id: uuid.UUID, *, active: bool = True, **overrides) -> dict:
    user = create_user(client, str(dealership_id), **overrides)
    if active:
        row = db.get(User, uuid.UUID(user["id"]))
        assert row is not None
        row.status = UserStatus.ACTIVE
        db.commit()
    return user


def headers_for(user: dict, dealership_id: uuid.UUID, *, manager: bool = True) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.UUID(user["id"]),
        tenant_id=dealership_id,
        group_id=dealer_group_of(dealership_id),
        roles=frozenset({AccessRole.SALES}),
        is_dealer_manager=manager,
    )
    return {"Authorization": f"Bearer {token}"}


def complete_branding(db: Session, dealership_id: uuid.UUID) -> None:
    db.add(
        BrandingAsset(
            dealership_id=dealership_id,
            kind=BrandingAssetKind.PRIMARY,
            object_key=f"branding/{uuid.uuid4()}.png",
            mime_type="image/png",
            byte_size=100,
        )
    )
    db.commit()


def complete_vat(db: Session, dealership_id: uuid.UUID) -> None:
    dealership = db.get(Dealership, dealership_id)
    assert dealership is not None
    dealership.vat_rate = Decimal("8.10")
    dealership.vat_liable = False
    db.commit()


def complete_bank_account(db: Session, dealership_id: uuid.UUID) -> DealershipBankAccount:
    row = DealershipBankAccount(
        dealership_id=dealership_id,
        account_holder="Garage Musterbetrieb AG",
        bank_name="Musterbank",
        iban=IBAN,
        qr_reference_type=QrReferenceType.NON,
        is_default=True,
    )
    db.add(row)
    db.commit()
    return row


def complete_number_ranges(db: Session, dealership_id: uuid.UUID, user_id: uuid.UUID) -> None:
    db.add(NumberRangeConfirmation(dealership_id=dealership_id, confirmed_by=user_id))
    db.commit()


def seat_location(db: Session, dealership_id: uuid.UUID) -> Location:
    location = db.scalar(select(Location).where(Location.tenant_id == dealership_id).order_by(Location.created_at))
    assert location is not None
    return location


def complete_opening_hours(db: Session, dealership_id: uuid.UUID) -> LocationOpeningHours:
    row = LocationOpeningHours(
        location_id=seat_location(db, dealership_id).id,
        department=LocationDepartment.SALES,
        weekday=1,
        opens_at=dt.time(8, 0),
        closes_at=dt.time(18, 0),
    )
    db.add(row)
    db.commit()
    return row


def complete_checklist(db: Session, dealership_id: uuid.UUID, manager_user: dict) -> None:
    complete_branding(db, dealership_id)
    complete_vat(db, dealership_id)
    complete_bank_account(db, dealership_id)
    complete_number_ranges(db, dealership_id, uuid.UUID(manager_user["id"]))
    complete_opening_hours(db, dealership_id)


def publish_document(
    db: Session,
    document_type: LegalDocumentType,
    version: str = "0.9",
    *,
    language: SwissLanguage = SwissLanguage.DE,
    effective_date: dt.date | None = None,
    grace_days: int = 30,
) -> LegalDocument:
    row = LegalDocument(
        document_type=document_type,
        version=version,
        language=language,
        effective_date=effective_date or dt.date(2026, 1, 1),
        title=f"{document_type.value} {version}",
        body_markdown=f"# {document_type.value} {version}\n\nText.",
        acceptance_grace_days=grace_days,
    )
    db.add(row)
    db.commit()
    return row


def publish_both(db: Session, version: str = "0.9", **kwargs) -> dict[LegalDocumentType, LegalDocument]:
    return {t: publish_document(db, t, version, **kwargs) for t in LegalDocumentType}


def accept_both(client: TestClient, dealership_id: uuid.UUID, documents, headers: dict[str, str]) -> None:
    for document in documents.values():
        response = client.post(
            f"/v1/dealerships/{dealership_id}/legal-acceptances",
            json={"legalDocumentId": str(document.id)},
            headers=headers,
        )
        assert response.status_code == 201, response.text
