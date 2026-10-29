"""A tenant's demo catalogue, built the only way a catalogue is built since
KAN-485: one single lookup per variant (`sync_catalogue_variant`), never a
full seed. The mock adapter knows these three variants.
"""

import uuid

from sqlalchemy.orm import Session

from app.vehicle.services import catalogue_sync

DEMO_FZ_KEYS = ("FZ100001", "FZ100002", "FZ100003")


def look_up_demo_variants(db: Session, tenant_id: uuid.UUID) -> None:
    for fz_key in DEMO_FZ_KEYS:
        skipped = catalogue_sync.sync_catalogue_variant(db, tenant_id=tenant_id, fz_key=fz_key)
        assert skipped is None, skipped
