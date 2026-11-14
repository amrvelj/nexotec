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

KAN-79 retired the legacy `vehicle` table, and with it this seed's legacy car,
its `vehicle_party` row, its `transaction` row and its custody event. None can
come back: the round trip's downgrade recreates `vehicle` empty and then
re-adds PR-2's foreign keys onto it (`vehicle_party.vehicle_id`,
`transaction.vehicle_id`), so any party or transaction row would break that
step. The cost, said here so nobody reads more into a green run: the round
trip no longer re-adds a foreign key over a `vehicle_party` or `transaction`
row; the dealership, user, customer chain and catalogue rows still carry it.

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

import uuid

import sqlalchemy as sa
from migration_smoke_test_catalogue import seed_catalogue_type_approval
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
from app.db import SessionLocal
from app.platform.models.dealership import DealerGroup, Dealership, DealershipStatus, FranchiseType

_DEALER_ADDRESS = {
    "address_street": "Bahnhofstrasse",
    "address_house_number": "1",
    "address_postal_code": "8001",
    "address_locality": "Zürich",
    "address_canton": "ZH",
    "address_country": "CH",
}


# Column sentinels (KAN-688: was one function each). The upgrade-from-previous
# CI job seeds against main's schema before this branch's migrations apply,
# while this branch's ORM classes already name every new column — the trap
# described in the module docstring. A table that existed before the column
# needs a column-level check: each entry is (table, the column whose presence
# says the migration has run), with the change that added it.
_SCHEMA_SENTINELS: dict[str, tuple[str, str]] = {
    # WP-6b PR-1 (logo_url, brand_primary_color, default_correspondence_language)
    "dealership_has_wp6b_columns": ("dealership", "logo_url"),
    # WP-7 PR-3/PR-7 (vat_rate, ageing_alert_thresholds)
    "dealership_has_wp7_columns": ("dealership", "vat_rate"),
    # KAN-205 (the branding columns; nullable or server default since)
    "dealership_has_kan205_columns": ("dealership", "brand_secondary_color"),
    # KAN-197 / D-A-22 (plain uid replaced the encrypted tax_id)
    "dealership_has_kan197_columns": ("dealership", "uid"),
    # WP-8 PR-6 (credit_block, credit_block_reason, credit_blocked_at)
    "customer_has_credit_block_column": ("customer", "credit_block"),
    # KAN-50 (the FR-17 fields; gender/newsletter/vat_registered NOT NULL)
    "customer_has_fr17_fields": ("customer", "gender"),
    # KAN-295 (vat_status_confirmed, NOT NULL with a Python-side default)
    "customer_has_vat_status_confirmed": ("customer", "vat_status_confirmed"),
    # KAN-52 / FR-23 §1 (consent_scope on the three contact-channel tables)
    "contact_channel_has_consent_scope": ("customer_phone", "consent_scope"),
}


def _has(db: Session, sentinel: str) -> bool:
    table, column = _SCHEMA_SENTINELS[sentinel]
    return column in {col["name"] for col in inspect(db.get_bind()).get_columns(table)}


def _live_columns_table(db: Session, model: type) -> sa.Table | None:
    """`None` while the live table holds every column `model` maps (the ORM
    class then addresses it directly); otherwise a table of only the live
    columns, with the class's own types and Python-side defaults, so a raw
    insert names nothing main's schema lacks (KAN-206 `email`/`website`,
    KAN-218 `offboarded_at`/`retention_until` on `dealership`). The values are
    the class's; a column the class no longer maps is the older branches'.
    """

    mapped = model.__table__  # type: ignore[attr-defined]
    live = {col["name"] for col in inspect(db.get_bind()).get_columns(mapped.name)}
    if {column.name for column in mapped.columns} <= live:
        return None
    columns = []
    for column in mapped.columns:
        if column.name not in live:
            continue
        default = column.default
        python_default = default.arg if default is not None and (default.is_scalar or default.is_callable) else None
        columns.append(sa.Column(column.name, column.type, default=python_default))
    return sa.Table(mapped.name, sa.MetaData(), *columns)


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
        # Values, not names: main stores .value since KAN-86 step 2, and since
        # KAN-175 every enum column's CHECK refuses a name.
        "role": "sales",
        "employment_status": "active",
        "status": "active",
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


# The two dealerships the KAN-197 migration is proven against: one whose old
# tax_id is a valid UID (converted) and one whose old tax_id fails the check
# digit (reported, uid left NULL). scripts/verify_migration_smoke_test_seed.py
# asserts both outcomes after the upgrade.
SMOKE_TEST_UID = "CHE-987.654.326"
SMOKE_TEST_INVALID_TAX_ID = "CHE-999.999.999"
SMOKE_TEST_INVALID_UID_LEGAL_NAME = "Migration Smoke Test Invalid UID GmbH"


def _seed_dealer_and_group_new_schema(db: Session) -> tuple[uuid.UUID, uuid.UUID]:
    """This PR's own heads already applied — dealer_group/dealership exist,
    so the current ORM classes address the real schema directly. Except
    the dealership columns a later PR added — see the `dealership_has_*`
    entries of _SCHEMA_SENTINELS.
    """

    group = DealerGroup(name="Migration Smoke Test Group")
    db.add(group)
    db.flush()

    if _has(db, "dealership_has_kan197_columns") and not _has(db, "dealership_has_kan205_columns"):
        # main's shape before KAN-205 (KAN-197 applied, with or without
        # KAN-203): a raw insert naming every column through KAN-198, and no
        # further — KAN-203's and KAN-205's columns are nullable or defaulted.
        kan197 = sa.table(
            "dealership",
            sa.column("id", GUID()),
            sa.column("dealer_group_id", GUID()),
            sa.column("legal_name", sa.String()),
            sa.column("franchise_type", sa.String()),
            sa.column("address_street", sa.String()),
            sa.column("address_house_number", sa.String()),
            sa.column("address_postal_code", sa.String()),
            sa.column("address_locality", sa.String()),
            sa.column("address_canton", sa.String()),
            sa.column("address_country", sa.String()),
            sa.column("phone", sa.String()),
            sa.column("uid", sa.String()),
            sa.column("vat_liable", sa.Boolean()),
            sa.column("status", sa.String()),
            sa.column("brand_primary_color", sa.String()),
            sa.column("default_correspondence_language", sa.String()),
            sa.column("entitlements_version", sa.Integer()),
            sa.column("version", sa.Integer()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        invalid_group = DealerGroup(name="Migration Smoke Test Invalid UID Group")
        db.add(invalid_group)
        db.flush()
        dealership_id = uuid7()
        for row_id, group_id, legal_name, uid in (
            (dealership_id, group.id, "Migration Smoke Test AG", SMOKE_TEST_UID),
            (uuid7(), invalid_group.id, SMOKE_TEST_INVALID_UID_LEGAL_NAME, None),
        ):
            db.execute(
                kan197.insert().values(
                    id=row_id,
                    dealer_group_id=group_id,
                    legal_name=legal_name,
                    franchise_type="independent",
                    **_DEALER_ADDRESS,
                    phone="+41441234567",
                    uid=uid,
                    vat_liable=False,
                    status="active",
                    brand_primary_color="#7C3AED",
                    default_correspondence_language="de",
                    entitlements_version=0,
                    version=1,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
        db.flush()
        return dealership_id, group.id

    if _has(db, "dealership_has_kan197_columns") and (live := _live_columns_table(db, Dealership)) is not None:
        # main's shape after KAN-205 but before this branch's own dealership
        # columns: the class's values, through the live columns only.
        invalid_group = DealerGroup(name="Migration Smoke Test Invalid UID Group")
        db.add(invalid_group)
        db.flush()
        dealership_id = uuid7()
        for row_id, group_id, legal_name, uid in (
            (dealership_id, group.id, "Migration Smoke Test AG", SMOKE_TEST_UID),
            (uuid7(), invalid_group.id, SMOKE_TEST_INVALID_UID_LEGAL_NAME, None),
        ):
            db.execute(
                live.insert().values(
                    id=row_id,
                    dealer_group_id=group_id,
                    legal_name=legal_name,
                    franchise_type=FranchiseType.INDEPENDENT,
                    status=DealershipStatus.ACTIVE,
                    phone="+41441234567",
                    uid=uid,
                    **_DEALER_ADDRESS,
                )
            )
        db.flush()
        return dealership_id, group.id

    if _has(db, "dealership_has_kan197_columns"):
        dealership = Dealership(
            dealer_group_id=group.id,
            legal_name="Migration Smoke Test AG",
            franchise_type=FranchiseType.INDEPENDENT,
            status=DealershipStatus.ACTIVE,
            phone="+41441234567",
            uid=SMOKE_TEST_UID,
            **_DEALER_ADDRESS,
        )
        # On the new schema the invalid value cannot be stored at all: the
        # row is written the way the migration leaves it, uid NULL. In its own
        # group of one: the trunk's customer migrations resolve a tenant from
        # the group ("one dealership per group") on downgrade, and they are
        # immutable.
        invalid_group = DealerGroup(name="Migration Smoke Test Invalid UID Group")
        db.add(invalid_group)
        db.flush()
        invalid = Dealership(
            dealer_group_id=invalid_group.id,
            legal_name=SMOKE_TEST_INVALID_UID_LEGAL_NAME,
            franchise_type=FranchiseType.INDEPENDENT,
            status=DealershipStatus.ACTIVE,
            phone="+41441234567",
            uid=None,
            **_DEALER_ADDRESS,
        )
        db.add_all([dealership, invalid])
        db.flush()
        return dealership.id, group.id

    if _has(db, "dealership_has_wp7_columns"):
        # main's current shape (before KAN-197): encrypted tax_id, the US
        # licence columns, dba_name, postal code 4. The ORM class no longer
        # has these columns, so this is a raw insert naming them; enum
        # columns hold the member's value (KAN-175).
        pre_kan197 = sa.table(
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
            sa.column("logo_url", sa.String()),
            sa.column("brand_primary_color", sa.String()),
            sa.column("default_correspondence_language", sa.String()),
            sa.column("version", sa.Integer()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        invalid_group = DealerGroup(name="Migration Smoke Test Invalid UID Group")
        db.add(invalid_group)
        db.flush()
        dealership_id = uuid7()
        for row_id, group_id, legal_name, tax_id in (
            (dealership_id, group.id, "Migration Smoke Test AG", SMOKE_TEST_UID),
            (uuid7(), invalid_group.id, SMOKE_TEST_INVALID_UID_LEGAL_NAME, SMOKE_TEST_INVALID_TAX_ID),
        ):
            db.execute(
                pre_kan197.insert().values(
                    id=row_id,
                    dealer_group_id=group_id,
                    legal_name=legal_name,
                    dealer_license_number="ZH-99999",
                    license_state="ZH",
                    franchise_type="independent",
                    **_DEALER_ADDRESS,
                    phone="+41441234567",
                    tax_id=tax_id,
                    status="active",
                    logo_url=None,
                    brand_primary_color="#7C3AED",
                    default_correspondence_language="de",
                    version=1,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
        db.flush()
        return dealership_id, group.id

    if _has(db, "dealership_has_wp6b_columns"):
        # WP-6b's columns are live but WP-7's (vat_rate/
        # ageing_alert_thresholds) aren't yet — the ORM class above already
        # carries those two, so it can't be used here; a raw insert naming
        # every column through WP-6b, and no further, is the only way to
        # write a row this PR's migrations haven't reached yet.
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
                sa.column("logo_url", sa.String()),
                sa.column("brand_primary_color", sa.String()),
                sa.column("default_correspondence_language", sa.String()),
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
                logo_url=None,
                brand_primary_color="#7C3AED",
                default_correspondence_language="DE",
                version=1,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        db.flush()
        return dealership_id, group.id

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


def _seed_fr17_customer_raw(db: Session, *, group_id: uuid.UUID) -> uuid.UUID:
    """The FR-17 customer shape (KAN-50) without KAN-295's column, written
    through the reflected table so only the columns main's schema really has
    are named. Values, not names: main writes .value since KAN-86 step 2."""

    customer_table = sa.Table("customer", sa.MetaData(), autoload_with=db.get_bind())
    customer_id = uuid7()
    now = utcnow()
    values = {
        "id": customer_id, "group_id": group_id, "customer_number": "K-000001", "customer_type": "individual",
        "language": "de", "first_name": "Anna", "last_name": "Muster", "lifecycle_status": "prospect",
        "gender": "unspecified", "marketing_consent": False, "credit_block": False, "newsletter": False,
        "vat_registered": False, "version": 1, "created_at": now, "updated_at": now,
    }
    db.execute(customer_table.insert().values({k: v for k, v in values.items() if k in customer_table.c}))
    return customer_id


def _seed_customer_chain_new_schema(db: Session, *, group_id: uuid.UUID) -> uuid.UUID:
    """This PR's own heads already applied — group_id exists everywhere
    tenant_id used to, so the current ORM classes address the real schema
    directly. Except WP-8 PR-6's and KAN-50's own new customer columns
    specifically — see the `customer_has_*` entries of _SCHEMA_SENTINELS.
    """

    if _has(db, "customer_has_fr17_fields") and not _has(db, "customer_has_vat_status_confirmed"):
        customer_id = _seed_fr17_customer_raw(db, group_id=group_id)
    elif _has(db, "customer_has_fr17_fields"):
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
        customer_id = customer.id
    elif _has(db, "customer_has_credit_block_column"):
        # credit_block is live but KAN-50's FR-17 columns are not — the ORM
        # Customer class above always sends gender/newsletter/vat_registered
        # (NOT NULL, Python-side defaults), so a raw insert naming every
        # column through WP-8's own customer shape, and no further, is the
        # only way to write a row this PR's migrations haven't reached yet.
        customer_id = uuid7()
        db.execute(
            sa.table(
                "customer",
                sa.column("id", GUID()),
                sa.column("group_id", GUID()),
                sa.column("customer_number", sa.String()),
                sa.column("customer_type", sa.String()),
                sa.column("language", sa.String()),
                sa.column("first_name", sa.String()),
                sa.column("last_name", sa.String()),
                sa.column("lifecycle_status", sa.String()),
                sa.column("marketing_consent", sa.Boolean()),
                sa.column("credit_block", sa.Boolean()),
                sa.column("version", sa.Integer()),
                sa.column("created_at", sa.DateTime(timezone=True)),
                sa.column("updated_at", sa.DateTime(timezone=True)),
            )
            .insert()
            .values(
                id=customer_id,
                group_id=group_id,
                customer_number="K-000001",
                customer_type="INDIVIDUAL",
                language="DE",
                first_name="Anna",
                last_name="Muster",
                lifecycle_status="PROSPECT",
                marketing_consent=False,
                credit_block=False,
                version=1,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        db.flush()
    else:
        # Neither credit_block nor KAN-50's columns are live yet — the
        # WP-3-PR-2 group-scoped shape only.
        customer_id = uuid7()
        db.execute(
            sa.table(
                "customer",
                sa.column("id", GUID()),
                sa.column("group_id", GUID()),
                sa.column("customer_number", sa.String()),
                sa.column("customer_type", sa.String()),
                sa.column("language", sa.String()),
                sa.column("first_name", sa.String()),
                sa.column("last_name", sa.String()),
                sa.column("lifecycle_status", sa.String()),
                sa.column("marketing_consent", sa.Boolean()),
                sa.column("version", sa.Integer()),
                sa.column("created_at", sa.DateTime(timezone=True)),
                sa.column("updated_at", sa.DateTime(timezone=True)),
            )
            .insert()
            .values(
                id=customer_id,
                group_id=group_id,
                customer_number="K-000001",
                customer_type="INDIVIDUAL",
                language="DE",
                first_name="Anna",
                last_name="Muster",
                lifecycle_status="PROSPECT",
                marketing_consent=False,
                version=1,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        db.flush()

    db.add(CustomerNumberSequence(group_id=group_id, next_value=2))

    if _has(db, "contact_channel_has_consent_scope"):
        db.add(
            CustomerPhone(
                group_id=group_id,
                customer_id=customer_id,
                phone_type=PhoneType.MOBILE,
                phone_e164="+41791234567",
                phone_normalised="41791234567",
                is_primary=True,
            )
        )
        db.add(
            CustomerEmail(
                group_id=group_id,
                customer_id=customer_id,
                email_type=EmailType.PERSONAL,
                email_address="anna@example.ch",
                is_primary=True,
            )
        )
    else:
        # KAN-52's consent_scope isn't live yet — the CustomerPhone /
        # CustomerEmail ORM classes always send it now, so raw-insert the
        # pre-KAN-52 ContactChannelMixin shape. valid_from / do_not_use /
        # consent_granted / is_primary are NOT NULL with no server default
        # (c7e2a91f4b06), so all four must be named; the rest are nullable.
        now = utcnow()
        db.execute(
            sa.table(
                "customer_phone",
                sa.column("id", GUID()),
                sa.column("group_id", GUID()),
                sa.column("customer_id", GUID()),
                sa.column("phone_type", sa.String()),
                sa.column("phone_e164", sa.String()),
                sa.column("phone_normalised", sa.String()),
                sa.column("is_primary", sa.Boolean()),
                sa.column("valid_from", sa.DateTime(timezone=True)),
                sa.column("do_not_use", sa.Boolean()),
                sa.column("consent_granted", sa.Boolean()),
                sa.column("created_at", sa.DateTime(timezone=True)),
                sa.column("updated_at", sa.DateTime(timezone=True)),
            )
            .insert()
            .values(
                id=uuid7(),
                group_id=group_id,
                customer_id=customer_id,
                phone_type="MOBILE",
                phone_e164="+41791234567",
                phone_normalised="41791234567",
                is_primary=True,
                valid_from=now,
                do_not_use=False,
                consent_granted=False,
                created_at=now,
                updated_at=now,
            )
        )
        db.execute(
            sa.table(
                "customer_email",
                sa.column("id", GUID()),
                sa.column("group_id", GUID()),
                sa.column("customer_id", GUID()),
                sa.column("email_type", sa.String()),
                sa.column("email_address", sa.String()),
                sa.column("is_primary", sa.Boolean()),
                sa.column("valid_from", sa.DateTime(timezone=True)),
                sa.column("do_not_use", sa.Boolean()),
                sa.column("consent_granted", sa.Boolean()),
                sa.column("created_at", sa.DateTime(timezone=True)),
                sa.column("updated_at", sa.DateTime(timezone=True)),
            )
            .insert()
            .values(
                id=uuid7(),
                group_id=group_id,
                customer_id=customer_id,
                email_type="PERSONAL",
                email_address="anna@example.ch",
                is_primary=True,
                valid_from=now,
                do_not_use=False,
                consent_granted=False,
                created_at=now,
                updated_at=now,
            )
        )

    db.add(
        CustomerExternalId(group_id=group_id, customer_id=customer_id, system_name="crm", external_id="CRM-1")
    )
    db.flush()
    return customer_id


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

        group_id: uuid.UUID | None
        if new_schema:
            dealer_id, group_id = _seed_dealer_and_group_new_schema(db)
        else:
            dealer_id = _seed_dealer_old_schema(db)
            group_id = None

        user_id = _seed_user(db, tenant_id=dealer_id)

        # group_id is set exactly when new_schema is; testing it rather than
        # new_schema lets the type checker see it is not None here.
        if group_id is not None:
            customer_id = _seed_customer_chain_new_schema(db, group_id=group_id)
        else:
            customer_id = _seed_customer_chain_old_schema(db, dealer_id=dealer_id)

        seed_catalogue_type_approval(db)

        db.commit()
        print(f"Seeded dealer={dealer_id} user={user_id} customer={customer_id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
