"""Companion to scripts/seed_migration_smoke_test.py (PR-3, CI only):
confirms the seeded rows survived an alembic upgrade untouched — same
values, same relationships, no duplicates, nothing silently dropped.

Looks everything up by the seed's fixed, known values (dealer legal name,
vehicle VIN, customer number) rather than by ID passed between CI steps —
keeps this script self-contained, no state to thread through the workflow.

Usage: DMS_DATABASE_URL=... DMS_TAX_ID_ENCRYPTION_KEY=... python scripts/verify_migration_smoke_test_seed.py
"""

import sys

from sqlalchemy import select

from app.customer.models.customer import Customer, CustomerEmail, CustomerExternalId, CustomerNumberSequence, CustomerPhone
from app.customer.models.vehicle_party import VehicleParty
from app.db import SessionLocal
from app.platform.models.dealer import Dealer
from app.platform.models.user import User
from app.sales.models.transaction import Transaction
from app.vehicle.models.vehicle import Vehicle, VehicleCustodyEvent

_EXPECTED_ROW_COUNTS = {
    Dealer: 1,
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
}


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

        dealer = db.scalar(select(Dealer).where(Dealer.legal_name == "Migration Smoke Test AG"))
        if dealer is None:
            fail("seeded dealer not found")
        if dealer.tax_id != "CHE-999.999.999":
            fail(f"dealer.tax_id round-tripped wrong: {dealer.tax_id!r}")

        customer = db.scalar(select(Customer).where(Customer.customer_number == "K-000001"))
        if customer is None:
            fail("seeded customer not found")
        if customer.tenant_id != dealer.id:
            fail("customer.tenant_id no longer points at the seeded dealer")

        vehicle = db.scalar(select(Vehicle).where(Vehicle.vin == "1HGCM82633A004352"))
        if vehicle is None:
            fail("seeded vehicle not found")
        if vehicle.current_custodian_partner_id != dealer.id:
            fail("vehicle.current_custodian_partner_id no longer points at the seeded dealer")

        transaction = db.scalar(select(Transaction).where(Transaction.tenant_id == dealer.id))
        if transaction is None:
            fail("seeded transaction not found")
        if transaction.customer_id != customer.id or transaction.vehicle_id != vehicle.id:
            fail("transaction's customer_id/vehicle_id no longer match the seeded rows")

        print("OK: all seeded rows survived the upgrade untouched.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
