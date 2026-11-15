"""KAN-402 (Anto, 2026-11-15, option A; ADR-081 as amended): each time the web
service starts — after every deploy and every restart — every object key a
platform row holds is checked against the object store, and a key whose
object is missing is reported in the reconciliation findings, with the same
alarm as the nightly run (CLAUDE.md rule 10), instead of being discovered by
the user (a contract printed without its signature).

Why at start-up and not nightly: production keeps these files on the web
service's persistent disk (a stop-gap until a Swiss S3 store), which the
worker cannot read; a missing object is what a deploy onto a wrong or empty
disk produces, so the moment after a start is when it is learnt.

Reports, never repairs: the row keeps its key. Read-only on the store (it
opens and closes each object, never writes). An export archive is not
checked: it expires and is deleted by its own lifecycle (KAN-218).
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.object_store import InvalidObjectKey, ObjectStore
from app.core.reconciliation import ON_START_CONTEXT_PREFIX, Finding, ReconciliationRun, record_findings
from app.platform.identity.models.user import User
from app.platform.organisation.models.branding_asset import BrandingAsset
from app.platform.organisation.models.publishing_profile import PublishingProfile

CONTEXT = f"{ON_START_CONTEXT_PREFIX}platform.objects"
_TARGET = "object_store"


@dataclass(frozen=True)
class _KeyColumn:
    table: str
    row_id: InstrumentedAttribute[uuid.UUID]
    key: InstrumentedAttribute[str | None] | InstrumentedAttribute[str]

    @property
    def label(self) -> str:
        return f"{self.table}.{self.key.key} names no stored object"


# Every platform column that holds an object-store key and is kept for good.
KEY_COLUMNS: tuple[_KeyColumn, ...] = (
    _KeyColumn("user", User.id, User.signature_image_ref),
    _KeyColumn("user", User.id, User.photo_ref),
    _KeyColumn("branding_asset", BrandingAsset.id, BrandingAsset.object_key),
    _KeyColumn("publishing_profile", PublishingProfile.id, PublishingProfile.agb_object_key),
)


def _held(store: ObjectStore, key: str) -> bool:
    try:
        opened = store.open(key)
    except InvalidObjectKey:
        return False  # a key the store cannot hold names nothing either
    if opened is None:
        return False
    opened.close()
    return True


def find_missing_objects(db: Session, store: ObjectStore) -> Iterator[Finding]:
    """Read-only: one finding per row whose key the store does not hold."""

    for column in KEY_COLUMNS:
        rows = db.execute(select(column.row_id, column.key).where(column.key.is_not(None)).order_by(column.row_id))
        for row_id, key in rows:
            if not _held(store, key):
                yield Finding(
                    check_label=column.label,
                    source_table=column.table,
                    source_row_id=row_id,
                    target_table=_TARGET,
                    dangling_value=row_id,
                )


def run(db: Session, store: ObjectStore) -> ReconciliationRun:
    """Records the run and its findings (`reconciliation_run` /
    `reconciliation_orphan`, context `startup.platform.objects`), commits,
    and raises ReconciliationAlarm when an object is missing."""

    return record_findings(db, context=CONTEXT, checks_run=len(KEY_COLUMNS), findings=list(find_missing_objects(db, store)))
