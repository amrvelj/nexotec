"""Shared set-up for tests that create a *manual* configuration.

Anto's ruling (2026-10-12, KAN-246/247): a manual configuration needs all
five coded fields — vehicle kind, fuel, body, drive, transmission — and each
is checked against its reference list (KAN-96). A test that creates one
seeds those lists and passes these values; it never relies on an unseeded
list or a missing field standing in for "not relevant here".
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.models.reference_data import ReferenceList, ReferenceValue

# snake_case, for `ConfigurationCreate(**MANUAL_CODED)` in service tests
MANUAL_CODED: dict[str, str] = {
    "vehicle_kind": "passenger_car",
    "fuel_type": "petrol",
    "body_style": "sedan",
    "drivetrain": "fwd",
    "transmission": "manual",
}
# camelCase, for a `POST /v1/configurations` body
MANUAL_CODED_JSON: dict[str, str] = {
    "vehicleKind": "passenger_car",
    "fuelType": "petrol",
    "bodyStyle": "sedan",
    "drivetrain": "fwd",
    "transmission": "manual",
}


def seed_reference_list(db: Session, list_code: str, *values: str, inactive: tuple[str, ...] = ()) -> None:
    """Seed one list with active `values` and retired `inactive` ones; labels
    equal the code in every language. A list already present is left as is."""

    if db.scalar(select(ReferenceList.id).where(ReferenceList.list_code == list_code)) is not None:
        return
    ref_list = ReferenceList(list_code=list_code)
    db.add(ref_list)
    db.flush()
    for code, active in [*((v, True) for v in values), *((v, False) for v in inactive)]:
        db.add(
            ReferenceValue(
                list_id=ref_list.id, value_code=code, label_de=code, label_fr=code, label_it=code,
                label_en=code, active=active,
            )
        )
    db.commit()


def seed_manual_coded_lists(db: Session) -> None:
    """The five coded lists, each holding (at least) the `MANUAL_CODED` value."""

    for list_code, code in MANUAL_CODED.items():
        seed_reference_list(db, list_code, code)
