"""Companion to scripts/seed_migration_smoke_test.py (PR-3, CI only):
confirms the seeded rows survived an alembic upgrade untouched — same
values, same relationships, no duplicates, nothing silently dropped.

Looks everything up by the seed's fixed, known values (dealer legal name,
vehicle VIN, customer number) rather than by ID passed between CI steps —
keeps this script self-contained, no state to thread through the workflow.

Always runs AFTER `alembic upgrade heads` (this PR's own migrations) — see
seed_migration_smoke_test.py's own docstring for why *that* script has to
juggle two schema states and this one doesn't: by the time this runs, the
schema always matches this PR's current ORM classes (Dealership, not
Dealer; Customer.group_id, not tenant_id).

Usage: DMS_DATABASE_URL=... DMS_TAX_ID_ENCRYPTION_KEY=... python scripts/verify_migration_smoke_test_seed.py
"""

import datetime as dt
import sys

from sqlalchemy import select

from app.customer.models.customer import Customer, CustomerEmail, CustomerExternalId, CustomerNumberSequence, CustomerPhone
from app.customer.models.vehicle_party import VehicleParty
from app.db import SessionLocal
from app.platform.models.dealership import Dealership
from app.platform.models.user import User
from app.sales.models.transaction import Transaction
from app.vehicle.models.catalogue import ModelVariant, TypeApproval, VariantTypeApproval
from app.vehicle.models.vehicle import Vehicle, VehicleCustodyEvent

_EXPECTED_ROW_COUNTS = {
    Dealership: 1,
    User: 1,
    Customer: 1,
    CustomerNumberSequence: 1,
    CustomerPhone: 1,
    CustomerEmail: 1,
    CustomerExternalId: 1,
    Vehicle: 1,
    VehicleParty: 1,
    VehicleCustodyEvent: 1,
    Transaction: 1,
    ModelVariant: 1,
    TypeApproval: 1,
    # The m2m migration's backfill (or the new-schema seed) makes exactly
    # one link for the one seeded (variant, Typenschein) pair.
    VariantTypeApproval: 1,
}

_CATALOGUE_TYPE_APPROVAL_NUMBER = "SMK001"
_CATALOGUE_VARIANT_NAME = "Smoke Variant 1.4 TB"
_CATALOGUE_FIRST_REGISTRATION_FROM = dt.date(2015, 6, 1)


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def main() -> None:
    db = SessionLocal()
    try:
        for model, expected in _EXPECTED_ROW_COUNTS.items():
            actual = len(db.scalars(select(model)).all())
            if actual != expected:
                fail(f"{model.__tablename__}: expected {expected} row(s), found {actual}")

        dealership = db.scalar(select(Dealership).where(Dealership.legal_name == "Migration Smoke Test AG"))
        if dealership is None:
            fail("seeded dealership not found")
        if dealership.tax_id != "CHE-999.999.999":
            fail(f"dealership.tax_id round-tripped wrong: {dealership.tax_id!r}")

        customer = db.scalar(select(Customer).where(Customer.customer_number == "K-000001"))
        if customer is None:
            fail("seeded customer not found")
        if customer.group_id != dealership.dealer_group_id:
            fail("customer.group_id no longer points at the seeded dealership's group")

        vehicle = db.scalar(select(Vehicle).where(Vehicle.vin == "1HGCM82633A004352"))
        if vehicle is None:
            fail("seeded vehicle not found")
        if vehicle.current_custodian_partner_id != dealership.id:
            fail("vehicle.current_custodian_partner_id no longer points at the seeded dealership")

        transaction = db.scalar(select(Transaction).where(Transaction.tenant_id == dealership.id))
        if transaction is None:
            fail("seeded transaction not found")
        if transaction.customer_id != customer.id or transaction.vehicle_id != vehicle.id:
            fail("transaction's customer_id/vehicle_id no longer match the seeded rows")

        approval = db.scalar(
            select(TypeApproval).where(TypeApproval.type_approval_number == _CATALOGUE_TYPE_APPROVAL_NUMBER)
        )
        if approval is None:
            fail("seeded type approval not found — number did not survive the m2m migration")
        variant = db.scalar(select(ModelVariant).where(ModelVariant.name == _CATALOGUE_VARIANT_NAME))
        if variant is None:
            fail("seeded model variant not found")
        links = approval.variant_links
        if len(links) != 1:
            fail(f"expected exactly one variant link on the seeded Typenschein, found {len(links)}")
        if links[0].model_variant_id != variant.id:
            fail("the backfilled link does not point at the seeded model variant")
        if links[0].first_registration_from != _CATALOGUE_FIRST_REGISTRATION_FROM:
            fail(
                "first_registration_from did not migrate onto the link: "
                f"{links[0].first_registration_from!r}"
            )

        print("OK: all seeded rows survived the upgrade untouched.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
