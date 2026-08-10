"""Migration smoke-test seed (PR-3, CI only): inserts one consistent row
into every table PR-2 touched, so the CI migration job's downgrade/upgrade
round trip exercises real data instead of an empty schema. An upgrade from
empty proves the DDL is syntactically valid; it proves nothing about
whether a downgrade can re-add a foreign key over data that would violate
it, which is the actual risk PR-2 flagged (eleven constraint names copied
verbatim, four resolved by runtime inspection).

Every row here is consistent on purpose — no dangling references. This
seed exists to prove the round trip works on healthy data, matching the
condition under which a real downgrade would ever be run (immediately
after the corresponding upgrade, before anything could have gone stale).
It is not a reconciliation test; see tests/test_reconciliation.py for that.

Inserts directly via the ORM, bypassing the service layer entirely — this
only needs rows to exist with the right foreign keys, not the business
validation those services also perform.

Usage: DMS_DATABASE_URL=... DMS_TAX_ID_ENCRYPTION_KEY=... python scripts/seed_migration_smoke_test.py
"""

import datetime as dt
import uuid

from app.core.auth import AccessRole
from app.customer.models.customer import (
    Customer,
    CustomerEmail,
    CustomerExternalId,
    CustomerNumberSequence,
    CustomerPhone,
    EmailType,
    PhoneType,
)
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.db import SessionLocal
from app.platform.models.dealer import Dealer, DealerStatus, FranchiseType
from app.platform.models.user import EmploymentStatus, User, UserRole, UserStatus
from app.sales.models.transaction import Transaction, TransactionStatus, TransactionType
from app.vehicle.models.vehicle import (
    CustodyEventType,
    RegistrationStatus,
    Vehicle,
    VehicleCondition,
    VehicleCustodyEvent,
    VehicleStatus,
)


def main() -> None:
    db = SessionLocal()
    try:
        dealer = Dealer(
            legal_name="Migration Smoke Test AG",
            dealer_license_number="ZH-99999",
            license_state="ZH",
            franchise_type=FranchiseType.INDEPENDENT,
            status=DealerStatus.ACTIVE,
            address_street="Bahnhofstrasse",
            address_house_number="1",
            address_postal_code="8001",
            address_locality="Zürich",
            address_canton="ZH",
            address_country="CH",
            phone="+41441234567",
            tax_id="CHE-999.999.999",
        )
        db.add(dealer)
        db.flush()

        user = User(
            tenant_id=dealer.id,
            first_name="Smoke",
            last_name="Test",
            email="smoke-test@example.ch",
            role=UserRole.SALES,
            access_role=AccessRole.SALES,
            employment_status=EmploymentStatus.ACTIVE,
            status=UserStatus.ACTIVE,
            auth_identity_id="smoke-test-sub",
        )
        db.add(user)
        db.flush()

        customer = Customer(
            tenant_id=dealer.id,
            customer_number="K-000001",
            first_name="Anna",
            last_name="Muster",
        )
        db.add(customer)
        db.flush()

        db.add(CustomerNumberSequence(tenant_id=dealer.id, next_value=2))
        db.add(
            CustomerPhone(
                tenant_id=dealer.id,
                customer_id=customer.id,
                phone_type=PhoneType.MOBILE,
                phone_e164="+41791234567",
                phone_normalised="41791234567",
                is_primary=True,
            )
        )
        db.add(
            CustomerEmail(
                tenant_id=dealer.id,
                customer_id=customer.id,
                email_type=EmailType.PRIVATE,
                email_address="anna@example.ch",
                is_primary=True,
            )
        )
        db.add(
            CustomerExternalId(
                tenant_id=dealer.id, customer_id=customer.id, system_name="crm", external_id="CRM-1"
            )
        )

        vehicle = Vehicle(
            vin="1HGCM82633A004352",
            make="Honda",
            model="Accord",
            model_year=2020,
            condition=VehicleCondition.USED,
            registration_status=RegistrationStatus.UNREGISTERED,
            status=VehicleStatus.IN_STOCK,
            current_custodian_partner_id=dealer.id,
        )
        db.add(vehicle)
        db.flush()

        db.add(VehicleParty(vehicle_id=vehicle.id, customer_id=customer.id, role=VehiclePartyRole.OWNER))

        transaction = Transaction(
            tenant_id=dealer.id,
            transaction_type=TransactionType.SALE,
            status=TransactionStatus.DRAFT,
            customer_id=customer.id,
            vehicle_id=vehicle.id,
            primary_user_id=user.id,
        )
        db.add(transaction)
        db.flush()

        db.add(
            VehicleCustodyEvent(
                vehicle_id=vehicle.id,
                partner_id=dealer.id,
                event_type=CustodyEventType.ACQUIRED,
                event_date=dt.datetime.now(dt.timezone.utc),
                transaction_id=transaction.id,
            )
        )

        db.commit()
        print(f"Seeded dealer={dealer.id} customer={customer.id} vehicle={vehicle.id} transaction={transaction.id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
