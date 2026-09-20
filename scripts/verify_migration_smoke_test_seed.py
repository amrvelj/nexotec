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
import importlib.util
import sys
from pathlib import Path

from sqlalchemy import select

from app.customer.models.customer import Customer, CustomerEmail, CustomerExternalId, CustomerNumberSequence, CustomerPhone
from app.customer.models.vehicle_party import VehicleParty
from app.db import SessionLocal
from app.platform.models.dealership import Dealership
from app.platform.models.user import User
from app.sales.models.transaction import Transaction
from app.vehicle.models.catalogue import ModelVariant, TypeApproval, VariantTypeApproval
from app.vehicle.models.provider import ProviderCodeMap
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


def _load_code_map_seed_migration():
    versions = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "vehicle"
    (path,) = sorted(versions.glob("7c4e9a2b6d13_*.py"))
    spec = importlib.util.spec_from_file_location("kan38_pr2c_code_map_seed", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verify_auto_i_dat_code_map(db) -> None:
    """KAN-38 PR 2c — the first CI assertion on seeded *reference content*
    (the row counts above prove execution, not content): after a real
    `alembic upgrade heads` every seeded auto_i_dat mapping is present with
    the seeded target, and CodeGrpNr 112 is a stroke count — never a
    drivetrain. Data-only seed, no column added, so the ORM-vs-schema trap
    this script's sibling has to dodge does not apply here.
    """

    seed = _load_code_map_seed_migration()
    rows = db.scalars(select(ProviderCodeMap).where(ProviderCodeMap.provider == seed.PROVIDER)).all()
    by_key = {(r.vehicle_kind, r.code_group, r.provider_code): (r.canonical_list_code, r.canonical_value_code) for r in rows}

    missing = [
        (kind, code_group, code)
        for kind, code_group, code, list_code, value_code in seed.SEED_ROWS
        if by_key.get((kind, code_group, code)) != (list_code, value_code)
    ]
    if missing:
        fail(f"auto_i_dat code map: {len(missing)} seeded mapping(s) missing or re-pointed after the upgrade, e.g. {missing[:3]}")

    if by_key.get(("03", "engine_cycle", "2")) != ("engine_cycle", "two_stroke"):
        fail("auto_i_dat code map: motorcycle Antrieb 2 (CodeGrpNr 112) is not mapped to engine_cycle/two_stroke")
    if any(kind == "03" and code_group == "drivetrain" for kind, code_group, _code in by_key):
        fail("auto_i_dat code map: a motorcycle (FzArt 03) has a drivetrain mapping — 112 is a stroke count (R-C-5)")
    if any(list_code == "engine_cycle" and (kind != "03" or code_group != "engine_cycle") for (kind, code_group, _c), (list_code, _v) in by_key.items()):
        fail("auto_i_dat code map: something other than motorcycle Antrieb maps into engine_cycle")


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

        _verify_auto_i_dat_code_map(db)

        print("OK: all seeded rows survived the upgrade untouched.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
