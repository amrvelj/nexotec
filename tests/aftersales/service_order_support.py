"""A Betrieb with the `aftersales` module, a VAT rate, a labour category with a
rate, contacts and a car — what a workshop order test needs (KAN-810)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.contact.models.contact import Contact, ContactLifecycleStatus
from app.core.auth import AccessRole, create_access_token
from app.core.money import PriceEntry
from app.core.permissions import ModuleCode
from app.platform.organisation.models.dealership import Dealership, Location
from app.platform.organisation.models.module_entitlement import ModuleEntitlement
from app.platform.organisation.services import labour_rates as rates
from app.platform.organisation.services import setting_store as store
from app.platform.organisation.services.labour_rate_settings import (
    RATES_ENTERED_KEY,
    TIME_UNIT_KEY,
    WORK_UNITS_PER_HOUR_KEY,
)
from app.vehicle.services import vehicle_mdm as vehicle_mdm_service
from tests.identity_support import create_dealership, create_user
from tests.token_support import dealer_group_of
from tests.vin_support import unique_vin


@dataclass
class Betrieb:
    id: uuid.UUID
    location_id: uuid.UUID
    category_id: uuid.UUID
    user_id: uuid.UUID

    @property
    def group_id(self) -> uuid.UUID:
        return dealer_group_of(self.id)


def headers(betrieb: Betrieb, *roles: AccessRole, entitlements: frozenset[str] | None = None,
            if_match: int | None = None, user_id: uuid.UUID | None = None) -> dict[str, str]:
    token = create_access_token(
        user_id=user_id or betrieb.user_id,
        tenant_id=betrieb.id,
        group_id=betrieb.group_id,
        roles=frozenset(roles or {AccessRole.AFTERSALES}),
        entitlements=entitlements,
    )
    out = {"Authorization": f"Bearer {token}"}
    if if_match is not None:
        out["If-Match"] = str(if_match)
    return out


def set_setting(db: Session, dealership_id: uuid.UUID, key: str, value, location_id: uuid.UUID | None = None) -> None:
    version = store.get_slot(db, dealership_id=dealership_id, location_id=location_id, key=key).version
    store.change_setting(
        db, dealership_id=dealership_id, location_id=location_id, key=key, value=value,
        expected_version=version, actor_id=None,
    )
    db.commit()


def work_units(db: Session, betrieb: Betrieb, per_hour: int) -> None:
    """O19 then O18: the Betrieb shows labour time in work units (ruling 183)."""

    set_setting(db, betrieb.id, WORK_UNITS_PER_HOUR_KEY, Decimal(per_hour))
    set_setting(db, betrieb.id, TIME_UNIT_KEY, "work_units")


def make_betrieb(client, db: Session, *, rate: str | None = "150.00", entered: PriceEntry = PriceEntry.NET,
                 vat_rate: str | None = "8.1", module: bool = True) -> Betrieb:
    dealership_id = uuid.UUID(create_dealership(client))
    if module:
        db.add(ModuleEntitlement(dealership_id=dealership_id, module=ModuleCode.AFTERSALES))
    if vat_rate is not None:
        db.execute(update(Dealership).where(Dealership.id == dealership_id).values(vat_rate=Decimal(vat_rate)))
    db.commit()
    location_id = db.scalar(select(Location.id).where(Location.tenant_id == dealership_id))
    assert location_id is not None
    set_setting(db, dealership_id, RATES_ENTERED_KEY, entered.value)
    category = rates.create_category(db, dealership_id=dealership_id, name="Mechanik", name_translations=None,
                                     actor_id=None)
    db.commit()
    if rate is not None:
        rates.create_rate(
            db, dealership_id=dealership_id, category_id=category.id, location_id=location_id,
            valid_from=rates.today(), amount=Decimal(rate), entered=entered, actor_id=None, on=rates.today(),
        )
        db.commit()
    advisor = create_user(client, str(dealership_id), firstName="Sandra", lastName="Kundendienst", role="service_advisor",
                          accessRoles=["aftersales"], isDealerManager=False)
    return Betrieb(id=dealership_id, location_id=location_id, category_id=category.id, user_id=uuid.UUID(advisor["id"]))


def make_contact(db: Session, betrieb: Betrieb, *, name: str = "Muster AG", blocked_reason: str | None = None,
                 status: ContactLifecycleStatus = ContactLifecycleStatus.ACTIVE,
                 group_id: uuid.UUID | None = None) -> Contact:
    contact = Contact(
        group_id=group_id or betrieb.group_id,
        contact_number=f"K-{uuid.uuid4().hex[:10]}",
        contact_type="business",
        company_name=name,
        language="de",
        lifecycle_status=status,
        credit_block=blocked_reason is not None,
        credit_block_reason=blocked_reason,
    )
    db.add(contact)
    db.commit()
    return contact


def make_vehicle(db: Session) -> uuid.UUID:
    record = vehicle_mdm_service.create_vehicle_mdm(db, vin=unique_vin(), catalogue_variant_id=None)
    db.commit()
    return record.id


def create_order(client, betrieb: Betrieb, contact_id: uuid.UUID, vehicle_id: uuid.UUID | None, **extra):
    body = {"locationId": str(betrieb.location_id), "contactId": str(contact_id),
            "vehicleId": str(vehicle_id) if vehicle_id else None, **extra}
    return client.post("/v1/service-orders", json=body, headers=headers(betrieb))


def new_order(client, db: Session, betrieb: Betrieb, **extra) -> dict:
    contact = make_contact(db, betrieb)
    response = create_order(client, betrieb, contact.id, make_vehicle(db), **extra)
    assert response.status_code == 201, response.text
    return response.json()


def add_job(client, betrieb: Betrieb, order: dict, name: str = "Service 30'000 km") -> dict:
    response = client.post(
        f"/v1/service-orders/{order['id']}/jobs", json={"name": name},
        headers=headers(betrieb, if_match=order["version"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def add_position(client, betrieb: Betrieb, order: dict, job_id: str, **body):
    return client.post(
        f"/v1/service-orders/{order['id']}/jobs/{job_id}/positions", json=body,
        headers=headers(betrieb, if_match=order["version"]),
    )


def labour_by_time(betrieb: Betrieb, time: str, **extra) -> dict:
    return {"kind": "labour_time", "text": "Arbeit", "labourCategoryId": str(betrieb.category_id), "time": time,
            **extra}
