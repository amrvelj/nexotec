"""A tenant's demo catalogue, built the way a catalogue is built since KAN-485
and KAN-211: a variant is stored as master data when it is seen (one single
lookup, never a full seed), and its details load once per Betrieb when the
car is opened (`catalogue_details.load_on_open`). The mock adapter knows
these three variants.
"""

import uuid

from sqlalchemy.orm import Session

from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.vehicle.models.catalogue import ModelVariant
from app.vehicle.models.catalogue_mirror import DetailState
from app.vehicle.services import catalogue_details, catalogue_sync

DEMO_FZ_KEYS = ("FZ100001", "FZ100002", "FZ100003")
MOCK_PROVIDER_CODE = "auto_i_dat_mock"


def see_demo_variant(db: Session, fz_key: str) -> ModelVariant:
    """The variant as identification or browse would store it: master data only."""

    master = MockAutoIDatAdapter().fetch_vehicle_master_data(fz_key)
    variant = catalogue_sync.upsert_model_variant(db, provider_code=MOCK_PROVIDER_CODE, master=master)
    db.commit()
    return variant


def open_demo_variant(db: Session, tenant_id: uuid.UUID, fz_key: str) -> ModelVariant:
    """Stores the variant and opens it for the Betrieb: every detail the
    Betrieb's entitlements allow is loaded (a denied licence option leaves
    its detail `not_in_contract`, as on a real account)."""

    variant = see_demo_variant(db, fz_key)
    catalogue_details.load_on_open(db, tenant_id=tenant_id, variant=variant, actor_id=None)
    return variant


def assert_all_present(db: Session, tenant_id: uuid.UUID, variant: ModelVariant) -> None:
    states = catalogue_details.detail_states(db, tenant_id=tenant_id, model_variant_id=variant.id)
    for detail in catalogue_details.DETAILS:
        row = states.state_of(detail)
        assert row is not None and row.state == DetailState.PRESENT, (detail, row and row.error)


def look_up_demo_variants(db: Session, tenant_id: uuid.UUID) -> None:
    for fz_key in DEMO_FZ_KEYS:
        open_demo_variant(db, tenant_id, fz_key)
