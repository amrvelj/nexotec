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
validation those services also perform. The one exception is `user`: this
script runs against TWO different schema states depending on which CI job
calls it — migration-smoke-test seeds AFTER upgrading to this PR's own
heads, migration-upgrade-from-previous seeds against `main`'s CURRENT
schema, BEFORE this PR's migrations apply. The `User` ORM class in this
PR's own code only knows the newest shape, so any migration that changes
one of its columns (WP-2 PR-2's access_role -> access_roles/
is_dealer_manager is the first case this repo has hit) breaks the ORM
insert in whichever job hasn't reached that migration yet. `_seed_user`
below inspects the actual columns on the table and branches — future-proof
against the next such migration too, not just this one.

Usage: DMS_DATABASE_URL=... DMS_TAX_ID_ENCRYPTION_KEY=... python scripts/seed_migration_smoke_test.py
"""

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.types import GUID
from app.core.uuid7 import uuid7
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
from app.sales.models.transaction import Transaction, TransactionStatus, TransactionType
from app.vehicle.models.vehicle import (
    CustodyEventType,
    RegistrationStatus,
    Vehicle,
    VehicleCondition,
    VehicleCustodyEvent,
    VehicleStatus,
)


def _seed_user(db: Session, *, tenant_id: uuid.UUID) -> uuid.UUID:
    """Raw insert, not the ORM User(...) constructor — see this module's
    own docstring for why. Returns the new row's id.
    """

    columns = {col["name"] for col in inspect(db.get_bind()).get_columns("user")}

    user_id = uuid7()
    now = utcnow()
    values: dict = {
        "id": user_id,
        "tenant_id": tenant_id,
        "first_name": "Smoke",
        "last_name": "Test",
        "email": "smoke-test@example.ch",
        "role": "SALES",
        "employment_status": "ACTIVE",
        "status": "ACTIVE",
        "auth_identity_id": "smoke-test-sub",
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    column_objs = [
        sa.column("id", GUID()),
        sa.column("tenant_id", GUID()),
        sa.column("first_name"),
        sa.column("last_name"),
        sa.column("email"),
        sa.column("role"),
        sa.column("employment_status"),
        sa.column("status"),
        sa.column("auth_identity_id"),
        sa.column("version"),
        sa.column("created_at"),
        sa.column("updated_at"),
    ]
    if "access_roles" in columns:
        # WP-2 PR-2 schema (this PR's own heads already applied).
        values["access_roles"] = ["sales"]
        values["is_dealer_manager"] = False
        column_objs += [sa.column("access_roles", sa.JSON), sa.column("is_dealer_manager", sa.Boolean)]
    else:
        # Pre-WP-2 schema (main's current state, before this PR's own
        # migration runs) — the column this PR drops still exists here.
        values["access_role"] = "SALES"
        column_objs.append(sa.column("access_role"))

    user_table = sa.table("user", *column_objs)
    db.execute(user_table.insert().values(**values))
    db.flush()
    return user_id


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

        user_id = _seed_user(db, tenant_id=dealer.id)

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
            primary_user_id=user_id,
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
