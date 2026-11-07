"""KAN-270 — how long a configuration no host took is kept (Anto,
2026-11-03: option A, then C on «what happens to one a host let go?»).

A configuration is created on the overlay's first save (`POST
/v1/configurations`); a host — an offer or contract (its car or its
trade-in), a stock item, a valuation — takes it in a separate call, and a
vehicle record holds the ones whose VIN links it (`vehicle_id`,
identification's `reusableConfigurationId`). Nothing ever removed one no
host took: the host's own commit failed after the save, or the response was
lost and the changed draft saved as a second one. ADR-068 gives them no
screen, and in `record` mode they hold a VIN and a mileage (revDSG).

The ruling, as built:

- **Taken once, kept for good.** `first_taken_at` is stamped the first time
  vehicle learns that a host took the configuration and is never cleared. A
  configuration a host took and let go (a draft offer switched to a stock
  car) stays.
- **Never taken: deleted 30 days after it was created**, by the daily job
  `vehicle.configurations.purge_unattached` (`app/vehicle/daily_jobs.py`),
  with its options and their feature links, an audit row (`delete`, reason
  `never_taken_purged`, never the VIN or mileage — the audit log is
  append-only) and `vehicle.configuration.discarded` (KAN-188's event, same
  payload), in vehicle's own transaction, one commit per batch.

**How vehicle learns it** without reading the hosts' tables (rule 2) or
writing inside their transactions (ADR-047): each host publishes a past-tense
fact through its outbox in the transaction that stores the id —
`sales.offer.configuration_attached`, `sales.contract.configuration_attached`,
`inventory.stock_item.added` / `inventory.stock_item.configuration_attached`
and `valuation.created`, each carrying `configurationId` — and vehicle's
consumers (`app/vehicle/consumers.py`) stamp it (`mark_configuration_taken`).

**The safety net:** every run asks each host context, through its read-only
`referenced_configuration_ids` on its `public` module, which of the unstamped
configurations it references now, and stamps those, as it does the ones a
vehicle record links. That covers a fact not consumed yet (the worker is
behind or the consumer is retrying) and every configuration a host held
before these facts were published — the first run after the release stamps
them all. A configuration a host took and let go before the release left no
trace and is purged like a never-taken one (nothing was live yet).

**A configuration linked to a known car counts as taken** (Anto, 2026-11-05,
KAN-567 option A; ADR-120): the vehicle register is one of a configuration's
hosts (ADR-068), so one whose VIN linked it to a car (`vehicle_id`) is stamped
and kept for good, even when no offer, contract, stock car or valuation took
it — nothing is deleted on a doubtful reading. The accepted cost: an orphaned
`record`-mode configuration of a known car (a failed or lost save) is never
cleaned up. A later rule that drops the link from the held set could no
longer tell these stamps from a real host's, so it would need its own marker.

**Residual race, accepted:** a configuration past 30 days that a host
attaches in the very moment the job runs — the host read it before the job
locked it, and commits its reference after the job asked it and before the
job's delete commits — is deleted under the host. The host then names a
configuration that no longer exists; the nightly reconciliation of Sales,
Stock and Valuation reports such a reference (rule 10), and the host's
`configuration_attached` fact stamps nothing. The window is the length of
one batch, at night, for a draft nobody touched for 30 days. Closing it
would need the host's read to lock the configuration row
(`get_configuration_for_host`), a vehicle lock inside the host's
transaction.
"""

import dataclasses
import datetime as dt
import uuid
from collections.abc import Callable, Sequence

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.vehicle.models.configuration import VehicleConfiguration, VehicleConfigurationOption
from app.vehicle.services.configuration import _ENTITY_TYPE, _publish, redact_personal_audit_values

RETENTION = dt.timedelta(days=30)
BATCH_SIZE = 200
PURGE_REASON = "never_taken_purged"
DISCARDED_EVENT = "vehicle.configuration.discarded"

# A host context's answer: which of these configuration ids its rows name.
HeldByHost = Callable[[Session, list[uuid.UUID]], set[uuid.UUID]]


@dataclasses.dataclass(frozen=True)
class PurgeResult:
    purged: int
    stamped: int


def mark_configuration_taken(
    db: Session, *, tenant_id: uuid.UUID, configuration_id: uuid.UUID, taken_at: dt.datetime
) -> bool:
    """Stamps `first_taken_at` unless it is set — never moved, never cleared.
    Only in the host's own Betrieb: a host reads a configuration of its own
    tenant only (`get_configuration_for_host`), so a fact naming another
    tenant's stamps nothing. An id naming nothing (purged, or never existed)
    stamps nothing either. `version` and `updated_at` are left alone: the
    stamp is vehicle's bookkeeping, not an edit a client must re-read.
    Returns True when this call stamped. Does not commit."""

    result = db.execute(
        update(VehicleConfiguration)
        .where(
            VehicleConfiguration.id == configuration_id,
            VehicleConfiguration.tenant_id == tenant_id,
            VehicleConfiguration.first_taken_at.is_(None),
        )
        .values(first_taken_at=taken_at, updated_at=VehicleConfiguration.updated_at)
        .execution_options(synchronize_session=False)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


def _stamp(db: Session, ids: set[uuid.UUID], now: dt.datetime) -> int:
    if not ids:
        return 0
    result = db.execute(
        update(VehicleConfiguration)
        .where(VehicleConfiguration.id.in_(list(ids)), VehicleConfiguration.first_taken_at.is_(None))
        .values(first_taken_at=now, updated_at=VehicleConfiguration.updated_at)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def purge_unattached_configurations(
    db: Session,
    *,
    held_by_hosts: Sequence[HeldByHost],
    now: dt.datetime | None = None,
    batch_size: int = BATCH_SIZE,
) -> PurgeResult:
    """Walks every unstamped configuration, oldest first, in batches (keyset
    on `created_at`, `id`; the partial index `ix_vehicle_configuration_
    untaken_created_at`). Per batch: the ones past `RETENTION` are locked
    (`FOR UPDATE SKIP LOCKED` — a second worker, or a configuration being
    saved, is left for the next run) and re-read; then the hosts are asked;
    every held one is stamped, every locked one still unstamped and unheld
    is deleted; one commit. A configuration younger than `RETENTION` is only
    ever stamped. A repeat run finds nothing to do."""

    now = now or utcnow()
    cutoff = now - RETENTION
    purged = stamped = 0
    after: tuple[dt.datetime, uuid.UUID] | None = None
    while True:
        query = select(VehicleConfiguration.id, VehicleConfiguration.created_at, VehicleConfiguration.vehicle_id).where(
            VehicleConfiguration.first_taken_at.is_(None)
        )
        if after is not None:
            last_created_at, last_id = after
            query = query.where(
                or_(
                    VehicleConfiguration.created_at > last_created_at,
                    and_(VehicleConfiguration.created_at == last_created_at, VehicleConfiguration.id > last_id),
                )
            )
        batch = db.execute(
            query.order_by(VehicleConfiguration.created_at, VehicleConfiguration.id).limit(batch_size)
        ).all()
        if not batch:
            break
        after = (batch[-1].created_at, batch[-1].id)

        expired_ids = [row.id for row in batch if row.created_at <= cutoff]
        locked: list[VehicleConfiguration] = []
        if expired_ids:
            locked = list(
                db.scalars(
                    select(VehicleConfiguration)
                    .where(VehicleConfiguration.id.in_(expired_ids), VehicleConfiguration.first_taken_at.is_(None))
                    .with_for_update(skip_locked=True, of=VehicleConfiguration)
                    .execution_options(populate_existing=True)
                )
            )

        # Asked after the lock, so a host attaching one of these between the
        # first read and the lock is seen (the module docstring's race is
        # what is left).
        ids = [row.id for row in batch]
        # KAN-567 (option A): a link to a car in the register holds it.
        held = {row.id for row in batch if row.vehicle_id is not None}
        held |= {config.id for config in locked if config.vehicle_id is not None}
        for held_by in held_by_hosts:
            held |= held_by(db, ids) & set(ids)
        stamped += _stamp(db, held, now)

        doomed = [config for config in locked if config.id not in held]
        if doomed:
            purged += _delete(db, [config.id for config in doomed])
        db.commit()
    return PurgeResult(purged=purged, stamped=stamped)


def _delete(db: Session, ids: list[uuid.UUID]) -> int:
    configurations = db.scalars(
        select(VehicleConfiguration)
        .where(VehicleConfiguration.id.in_(ids))
        .options(
            selectinload(VehicleConfiguration.options).selectinload(VehicleConfigurationOption.equipment_feature_links)
        )
    ).all()
    for configuration in configurations:
        record_audit_event(
            db,
            entity_type=_ENTITY_TYPE,
            entity_id=configuration.id,
            tenant_id=configuration.tenant_id,
            action="delete",
            actor_id=None,
            # What the configuration was, never what it held: no VIN, no
            # mileage, no plate — the audit log is append-only, so a copy
            # here would outlive the purge it records.
            before={
                "source": configuration.source.value,
                "mode": configuration.mode.value,
                "createdAt": configuration.created_at.isoformat(),
            },
            reason=PURGE_REASON,
        )
        # ADR-121 (KAN-564): its earlier rows copied the VIN, plate, mileage
        # and notes (update, VIN repair); the purge blanks them there too, so
        # nothing personal outlives the configuration.
        redact_personal_audit_values(db, configuration=configuration, actor_id=None, reason=PURGE_REASON)
        _publish(db, configuration, DISCARDED_EVENT)
        db.delete(configuration)
    db.flush()
    return len(configurations)
