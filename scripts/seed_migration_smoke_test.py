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
validation those services also perform. The one exception is `user`, and
now the whole dealer/dealership + customer/group chain too: this script
runs against TWO different schema states depending on which CI job calls
it — migration-smoke-test seeds AFTER upgrading to this PR's own heads,
migration-upgrade-from-previous seeds against `main`'s CURRENT schema,
BEFORE this PR's migrations apply. The ORM classes in this PR's own code
only know the newest shape, so any migration that renames a table or a
column breaks the ORM insert in whichever job hasn't reached that
migration yet:
  - WP-2 PR-2: user.access_role -> access_roles/is_dealer_manager
  - WP-3 PR-1: dealer table -> dealership (+ new NOT NULL dealer_group_id)
  - WP-3 PR-2: customer/customer_number_sequence/customer_phone/
    customer_email/customer_external_id: tenant_id -> group_id
`_seed_user` and `_seed_dealer_and_group`/`_seed_customer_chain` below all
follow the same pattern: detect which schema is actually live (table/
column presence), and use a raw `sa.table()` insert for the old shape
since this PR's ORM classes literally cannot address a table that no
longer exists under that name (`Dealer`) or a column that's been renamed
away (`Customer.tenant_id`). The new-schema branch uses this PR's own ORM
classes directly, since there schema and code genuinely agree.

Usage: DMS_DATABASE_URL=... DMS_TAX_ID_ENCRYPTION_KEY=... python scripts/seed_migration_smoke_test.py
"""

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.types import GUID, EncryptedString
from app.core.uuid7 import uuid7
from app.customer.models.customer import (
    Customer,
    CustomerEmail,
    CustomerExternalId,
    CustomerLifecycleStatus,
    CustomerNumberSequence,
    CustomerPhone,
    CustomerType,
    EmailType,
    Language,
    PhoneType,
)
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.db import SessionLocal
from app.platform.models.dealership import DealerGroup, Dealership, DealershipStatus, FranchiseType
from app.sales.models.transaction import Transaction, TransactionStatus, TransactionType
from app.vehicle.models.vehicle import (
    CustodyEventType,
    RegistrationStatus,
    Vehicle,
    VehicleCondition,
    VehicleCustodyEvent,
    VehicleStatus,
)

_DEALER_ADDRESS = {
    "address_street": "Bahnhofstrasse",
    "address_house_number": "1",
    "address_postal_code": "8001",
    "address_locality": "Zürich",
    "address_canton": "ZH",
    "address_country": "CH",
}


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


def _dealership_has_wp6b_columns(db: Session) -> bool:
    """WP-6b PR-1 added logo_url/brand_primary_color/
    default_correspondence_language to dealership. The
    migration-upgrade-from-previous CI job seeds against main's CURRENT
    schema before this PR's own migrations apply, same "ORM classes only
    know the newest shape" trap _seed_dealer_old_schema already exists to
    avoid — the dealership TABLE has existed since WP-3, so the coarser
    "dealership" in get_table_names() check that already gates the
    dealer/dealership rename doesn't catch this; a column-level check does.
    """

    columns = {col["name"] for col in inspect(db.get_bind()).get_columns("dealership")}
    return "logo_url" in columns


def _seed_dealer_and_group_new_schema(db: Session) -> tuple[uuid.UUID, uuid.UUID]:
    """This PR's own heads already applied — dealer_group/dealership exist,
    so the current ORM classes address the real schema directly. Except
    WP-6b's three new dealership columns specifically — see
    _dealership_has_wp6b_columns.
    """

    group = DealerGroup(name="Migration Smoke Test Group")
    db.add(group)
    db.flush()

    if _dealership_has_wp6b_columns(db):
        dealership = Dealership(
            dealer_group_id=group.id,
            legal_name="Migration Smoke Test AG",
            dealer_license_number="ZH-99999",
            license_state="ZH",
            franchise_type=FranchiseType.INDEPENDENT,
            status=DealershipStatus.ACTIVE,
            phone="+41441234567",
            tax_id="CHE-999.999.999",
            **_DEALER_ADDRESS,
        )
        db.add(dealership)
        db.flush()
        return dealership.id, group.id

    dealership_id = uuid7()
    db.execute(
        sa.table(
            "dealership",
            sa.column("id", GUID()),
            sa.column("dealer_group_id", GUID()),
            sa.column("legal_name", sa.String()),
            sa.column("dealer_license_number", sa.String()),
            sa.column("license_state", sa.String()),
            sa.column("franchise_type", sa.String()),
            sa.column("address_street", sa.String()),
            sa.column("address_house_number", sa.String()),
            sa.column("address_postal_code", sa.String()),
            sa.column("address_locality", sa.String()),
            sa.column("address_canton", sa.String()),
            sa.column("address_country", sa.String()),
            sa.column("phone", sa.String()),
            sa.column("tax_id", EncryptedString()),
            sa.column("status", sa.String()),
            sa.column("version", sa.Integer()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        .insert()
        .values(
            id=dealership_id,
            dealer_group_id=group.id,
            legal_name="Migration Smoke Test AG",
            dealer_license_number="ZH-99999",
            license_state="ZH",
            franchise_type="INDEPENDENT",
            **_DEALER_ADDRESS,
            phone="+41441234567",
            tax_id="CHE-999.999.999",
            status="ACTIVE",
            version=1,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    db.flush()
    return dealership_id, group.id


def _seed_dealer_old_schema(db: Session) -> uuid.UUID:
    """main's current state — table is still "dealer", no dealer_group_id
    column at all yet. Raw insert: this PR's Dealership ORM class is bound
    to __tablename__ = "dealership" and cannot target the pre-rename table.
    """

    now = utcnow()
    dealer_id = uuid7()
    dealer_table = sa.table(
        "dealer",
        sa.column("id", GUID()),
        sa.column("legal_name", sa.String()),
        sa.column("dealer_license_number", sa.String()),
        sa.column("license_state", sa.String()),
        sa.column("franchise_type", sa.String()),
        sa.column("address_street", sa.String()),
        sa.column("address_house_number", sa.String()),
        sa.column("address_postal_code", sa.String()),
        sa.column("address_locality", sa.String()),
        sa.column("address_canton", sa.String()),
        sa.column("address_country", sa.String()),
        sa.column("phone", sa.String()),
        sa.column("tax_id", EncryptedString()),
        sa.column("status", sa.String()),
        sa.column("version", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    db.execute(
        dealer_table.insert().values(
            id=dealer_id,
            legal_name="Migration Smoke Test AG",
            dealer_license_number="ZH-99999",
            license_state="ZH",
            # SQLAlchemy's Enum(native_enum=False) persists the member NAME,
            # not its value (confirmed against the original create_table
            # calls) — "INDEPENDENT"/"ACTIVE" here, not "independent"/"active".
            franchise_type="INDEPENDENT",
            **_DEALER_ADDRESS,
            phone="+41441234567",
            tax_id="CHE-999.999.999",
            status="ACTIVE",
            version=1,
            created_at=now,
            updated_at=now,
        )
    )
    db.flush()
    return dealer_id


def _seed_customer_chain_new_schema(db: Session, *, group_id: uuid.UUID) -> uuid.UUID:
    """This PR's own heads already applied — group_id exists everywhere
    tenant_id used to, so the current ORM classes address the real schema
    directly.
    """

    customer = Customer(
        group_id=group_id,
        customer_number="K-000001",
        customer_type=CustomerType.INDIVIDUAL,
        language=Language.DE,
        first_name="Anna",
        last_name="Muster",
        lifecycle_status=CustomerLifecycleStatus.PROSPECT,
    )
    db.add(customer)
    db.flush()

    db.add(CustomerNumberSequence(group_id=group_id, next_value=2))
    db.add(
        CustomerPhone(
            group_id=group_id,
            customer_id=customer.id,
            phone_type=PhoneType.MOBILE,
            phone_e164="+41791234567",
            phone_normalised="41791234567",
            is_primary=True,
        )
    )
    db.add(
        CustomerEmail(
            group_id=group_id,
            customer_id=customer.id,
            email_type=EmailType.PERSONAL,
            email_address="anna@example.ch",
            is_primary=True,
        )
    )
    db.add(
        CustomerExternalId(group_id=group_id, customer_id=customer.id, system_name="crm", external_id="CRM-1")
    )
    db.flush()
    return customer.id


def _seed_customer_chain_old_schema(db: Session, *, dealer_id: uuid.UUID) -> uuid.UUID:
    """main's current state — customer/customer_number_sequence/
    customer_phone/customer_email/customer_external_id are all still
    tenant_id-scoped, and none of them has PR-5's new contact-channel
    columns (label/isPrimary-per-type/validFrom/doNotUse/consent) yet.
    Raw insert for all five tables, same reasoning as _seed_dealer_old_schema.
    """

    now = utcnow()
    customer_id = uuid7()

    db.execute(
        sa.table(
            "customer",
            sa.column("id", GUID()),
            sa.column("tenant_id", GUID()),
            sa.column("customer_number", sa.String()),
            sa.column("first_name", sa.String()),
            sa.column("last_name", sa.String()),
            sa.column("customer_type", sa.String()),
            sa.column("language", sa.String()),
            sa.column("lifecycle_status", sa.String()),
            sa.column("marketing_consent", sa.Boolean()),
            sa.column("version", sa.Integer()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        .insert()
        .values(
            id=customer_id,
            tenant_id=dealer_id,
            customer_number="K-000001",
            first_name="Anna",
            last_name="Muster",
            customer_type="INDIVIDUAL",
            language="DE",
            lifecycle_status="PROSPECT",
            marketing_consent=False,
            version=1,
            created_at=now,
            updated_at=now,
        )
    )

    db.execute(
        sa.table(
            "customer_number_sequence",
            sa.column("tenant_id", GUID()),
            sa.column("next_value", sa.Integer()),
        )
        .insert()
        .values(tenant_id=dealer_id, next_value=2)
    )

    db.execute(
        sa.table(
            "customer_phone",
            sa.column("id", GUID()),
            sa.column("tenant_id", GUID()),
            sa.column("customer_id", GUID()),
            sa.column("phone_type", sa.String()),
            sa.column("phone_e164", sa.String()),
            sa.column("phone_normalised", sa.String()),
            sa.column("is_primary", sa.Boolean()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        .insert()
        .values(
            id=uuid7(),
            tenant_id=dealer_id,
            customer_id=customer_id,
            phone_type="MOBILE",
            phone_e164="+41791234567",
            phone_normalised="41791234567",
            is_primary=True,
            created_at=now,
            updated_at=now,
        )
    )

    db.execute(
        sa.table(
            "customer_email",
            sa.column("id", GUID()),
            sa.column("tenant_id", GUID()),
            sa.column("customer_id", GUID()),
            sa.column("email_type", sa.String()),
            sa.column("email_address", sa.String()),
            sa.column("is_primary", sa.Boolean()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        .insert()
        .values(
            id=uuid7(),
            tenant_id=dealer_id,
            customer_id=customer_id,
            # Pre-WP-3-PR-5 name: PRIVATE (renamed to PERSONAL by that
            # migration, which hasn't run yet at seed time in this branch).
            email_type="PRIVATE",
            email_address="anna@example.ch",
            is_primary=True,
            created_at=now,
            updated_at=now,
        )
    )

    db.execute(
        sa.table(
            "customer_external_id",
            sa.column("id", GUID()),
            sa.column("tenant_id", GUID()),
            sa.column("customer_id", GUID()),
            sa.column("system_name", sa.String()),
            sa.column("external_id", sa.String()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        .insert()
        .values(
            id=uuid7(),
            tenant_id=dealer_id,
            customer_id=customer_id,
            system_name="crm",
            external_id="CRM-1",
            created_at=now,
            updated_at=now,
        )
    )
    db.flush()
    return customer_id


def main() -> None:
    db = SessionLocal()
    try:
        new_schema = "dealership" in set(inspect(db.get_bind()).get_table_names())

        if new_schema:
            dealer_id, group_id = _seed_dealer_and_group_new_schema(db)
        else:
            dealer_id = _seed_dealer_old_schema(db)
            group_id = None

        user_id = _seed_user(db, tenant_id=dealer_id)

        if new_schema:
            customer_id = _seed_customer_chain_new_schema(db, group_id=group_id)
        else:
            customer_id = _seed_customer_chain_old_schema(db, dealer_id=dealer_id)

        vehicle = Vehicle(
            vin="1HGCM82633A004352",
            make="Honda",
            model="Accord",
            model_year=2020,
            condition=VehicleCondition.USED,
            registration_status=RegistrationStatus.UNREGISTERED,
            status=VehicleStatus.IN_STOCK,
            current_custodian_partner_id=dealer_id,
        )
        db.add(vehicle)
        db.flush()

        db.add(VehicleParty(vehicle_id=vehicle.id, customer_id=customer_id, role=VehiclePartyRole.OWNER))

        transaction = Transaction(
            tenant_id=dealer_id,
            transaction_type=TransactionType.SALE,
            status=TransactionStatus.DRAFT,
            customer_id=customer_id,
            vehicle_id=vehicle.id,
            primary_user_id=user_id,
        )
        db.add(transaction)
        db.flush()

        db.add(
            VehicleCustodyEvent(
                vehicle_id=vehicle.id,
                partner_id=dealer_id,
                event_type=CustodyEventType.ACQUIRED,
                event_date=dt.datetime.now(dt.timezone.utc),
                transaction_id=transaction.id,
            )
        )

        db.commit()
        print(f"Seeded dealer={dealer_id} customer={customer_id} vehicle={vehicle.id} transaction={transaction.id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
