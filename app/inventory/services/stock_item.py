"""StockItem service layer (WP-7 PR-1)."""

import hashlib
import uuid

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.outbox import OutboxEvent, publish
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.core.validators import normalise_vin
from app.inventory.models.stock_item import (
    AgeingBucket,
    LifecycleStatus,
    StockItem,
    StockItemCondition,
    StockNumberSequence,
)
from app.inventory.schemas.stock_item import StockItemCreate, StockItemUpdate
from app.inventory.services.vehicle_resolution import resolve_vehicle_mdm_by_vin
from app.vehicle.public import CONFIGURATION_MODE_BUILD, CONFIGURATION_MODE_RECORD, get_configuration_for_host

_EVENT_PRODUCER = "inventory"


def compute_ageing_bucket(item: StockItem) -> AgeingBucket | None:
    """None while the item has never been in_stock (pipeline) — ageing is
    "days on the lot," not "days since this row was created" (a factory
    order's time in the pipeline doesn't count).
    """

    if item.in_stock_at is None:
        return None
    days = (utcnow() - item.in_stock_at).days
    if days <= 60:
        return AgeingBucket.GREEN
    if days <= 120:
        return AgeingBucket.AMBER
    return AgeingBucket.RED


def allocate_stock_number(db: Session, tenant_id: uuid.UUID) -> str:
    """Row-lock-then-increment, one counter per tenant — same idiom as
    app.customer's per-group CustomerNumberSequence and app.vehicle's
    global VehicleNumberSequence, scoped to the dealership here because a
    stock number is this dealership's own stock, not a group or global
    fact.
    """

    row = db.get(StockNumberSequence, tenant_id, with_for_update=True)
    if row is None:
        # First use of this key. A concurrent first caller can insert the same
        # row: its commit turns our INSERT into a UniqueViolation (KAN-70). The
        # savepoint keeps the caller's transaction alive through that, and the
        # locked re-read below then waits for and takes the winner's row.
        # Flush the caller's own pending rows first, so that the except below
        # can only ever see this counter row's INSERT.
        db.flush()
        try:
            with db.begin_nested():
                db.add(StockNumberSequence(tenant_id=tenant_id, next_value=1))
        except IntegrityError:
            pass  # the concurrent caller's row now exists; re-read it below
        row = db.get(StockNumberSequence, tenant_id, with_for_update=True)
        assert row is not None, "StockNumberSequence row missing after its first-use INSERT or the concurrent winner's"

    value = row.next_value
    row.next_value += 1
    db.flush()
    return f"S-{value:06d}"


def get_stock_item_or_404(db: Session, tenant_id: uuid.UUID, stock_item_id: uuid.UUID) -> StockItem:
    item = db.scalar(
        select(StockItem).where(StockItem.id == stock_item_id, StockItem.tenant_id == tenant_id)
    )
    if item is None:
        raise NotFoundError(f"Stock item {stock_item_id} was not found.")
    return item


def get_stock_item_for_update_or_404(db: Session, tenant_id: uuid.UUID, stock_item_id: uuid.UUID) -> StockItem:
    """KAN-429: the item locked until the caller commits, as Sales' offer
    and contract routes load theirs (KAN-311). Every route that checks
    If-Match against the item loads it this way, so two saves sent with the
    same version run one after the other and the second is refused as
    stale; unlocked, both passed the check and the later one overwrote the
    first. The item is the only row these routes lock — the reservation
    writers take the same single row — so no lock order can cross. FOR NO
    KEY UPDATE: it conflicts with every other writer of the row but not with
    the child rows (options, ledger, media) others insert against it. A
    function of its own, not a flag on the read above, so a read stays a
    read (tests/architecture/test_adr_047_no_shared_transaction.py)."""

    item = db.scalar(
        select(StockItem)
        .where(StockItem.id == stock_item_id, StockItem.tenant_id == tenant_id)
        .with_for_update(key_share=True)
    )
    if item is None:
        raise NotFoundError(f"Stock item {stock_item_id} was not found.")
    return item


def _item_in_stock_with_vin(db: Session, *, tenant_id: uuid.UUID, vin: str) -> StockItem | None:
    """KAN-111: the dealership's item holding this VIN that has not left
    stock — what `uq_stock_item_tenant_id_vin_in_stock` allows at most one
    of. An item that left stock (invoiced, FR-I-12) does not count; a
    storno'd one still does (Anto, 2026-10-07)."""

    return db.scalar(
        select(StockItem).where(
            StockItem.tenant_id == tenant_id, StockItem.vin == vin, StockItem.left_stock_at.is_(None)
        )
    )


def _vin_already_in_stock(held_by: StockItem) -> ConflictError:
    """The one refusal for a second item with a VIN already in stock —
    `reason` for the UI to localise, the holder so it can link to it."""

    return ConflictError(
        f"This VIN is already in stock as {held_by.stock_number}.",
        details={
            "reason": "vin_already_in_stock",
            "stockItemId": str(held_by.id),
            "stockNumber": held_by.stock_number,
        },
    )


def configuration_vin_differs(configuration_vin: str | None, vin: str | None) -> bool:
    """KAN-463, KAN-510: the configuration names one car and the stock item
    (or the VIN typed for it) another. Only when both carry one: a
    configuration without a VIN (a factory order, `build`) takes the car's.
    Compared in the one spelling (`normalise_vin`, KAN-244), so a VIN typed
    in lower case or in groups is the same VIN."""

    if configuration_vin is None or vin is None:
        return False
    return normalise_vin(configuration_vin) != normalise_vin(vin)


def configuration_vin_differs_error(*, field: str = "configurationId") -> UnprocessableEntityError:
    """KAN-463: a configuration whose VIN is not the stock item's describes
    another car; linking it would show that car's technical data. `field`
    names what the request got wrong: the configuration it links, or (purchase
    booking, KAN-510) the VIN it typed."""

    return UnprocessableEntityError(
        "The configuration's VIN is not this stock item's VIN.",
        details={"field": field, "reason": "configuration_vin_differs"},
    )


def _lock_configuration(db: Session, *, tenant_id: uuid.UUID, configuration_id: uuid.UUID) -> None:
    """KAN-509: pg_advisory_xact_lock on a 64-bit key derived from (tenant,
    configuration), released by the transaction's commit or rollback. Create
    and attach take it before they look for another holder, so two writers
    linking one configuration to two items run one after the other and the
    second sees the first's item. SQLite (the fast local lane, ADR-011) has
    no advisory locks and serialises writers on its own, so this is a no-op
    there. Same idiom as app.inventory.services.reservation.lock_contract.
    """

    if db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"inventory.configuration:{tenant_id}:{configuration_id}".encode()).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _check_configuration_not_held(db: Session, *, tenant_id: uuid.UUID, configuration_id: uuid.UUID) -> None:
    """KAN-509 (FR-C-11): a configuration is one car's technical data, so
    two stock items never share one — editing it would change both cars.
    Only another *stock item* holding it is refused: the same configuration
    legitimately has an offer, a contract and that one item as hosts
    (FR-I-02). The caller holds `_lock_configuration`."""

    held_by = db.scalar(
        select(StockItem)
        .where(StockItem.tenant_id == tenant_id, StockItem.configuration_id == configuration_id)
        .order_by(StockItem.created_at)
        .limit(1)
    )
    if held_by is not None:
        raise ConflictError(
            f"This configuration already belongs to stock item {held_by.stock_number}.",
            details={
                "reason": "configuration_held_by_stock_item",
                "stockItemId": str(held_by.id),
                "stockNumber": held_by.stock_number,
            },
        )


def _check_vin_not_in_stock(db: Session, *, tenant_id: uuid.UUID, vin: str) -> None:
    """Refuse before writing, so the ordinary duplicate is a 409 with a
    reason. Two writers can still race past it; the unique index then
    refuses, and the callers map that to the same 409."""

    held_by = _item_in_stock_with_vin(db, tenant_id=tenant_id, vin=vin)
    if held_by is not None:
        raise _vin_already_in_stock(held_by)


def _build_and_flush_stock_item(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    data: StockItemCreate,
    actor_id: uuid.UUID | None,
    pipeline_ref: str | None,
    origin: dict[str, str] | None = None,
    configuration_label: str | None = None,
) -> StockItem:
    """The commit-free core, shared by create_stock_item (HTTP path, owns
    its own commit) and app.inventory.services.pipeline's consumer path
    (WP-7 PR-2, whose commit must be the SAME transaction as the outbox
    consumer harness's ProcessedEvent row — app.core.consumer's own "one
    rule": the side effect and its processed_event row are written
    together, or not at all).
    """

    item = StockItem(
        tenant_id=tenant_id,
        stock_number=allocate_stock_number(db, tenant_id),
        # With a VIN, create_stock_item has resolved this from vehicle-mdm
        # (KAN-244); without one StockItemCreate refuses it (KAN-324), so a
        # pipeline item is created with no vehicle and promotion can give it one.
        vehicle_id=data.vehicle_id,
        vin=data.vin,
        vehicle_label=data.vehicle_label,
        condition=data.condition,
        location_id=data.location_id,
        odometer_km=data.odometer_km,
        list_price=data.list_price,
        effective_price=data.effective_price,
        first_registration_date=data.first_registration_date,
        pipeline_ref=pipeline_ref,
        configuration_id=data.configuration_id,
        configuration_label=configuration_label if data.configuration_id is not None else None,
        configuration_label_refreshed_at=utcnow() if data.configuration_id is not None else None,
        # A stock item created directly (not via the pipeline/promotion
        # path, PR-2) already has a VIN in hand — it goes straight to
        # in_stock rather than sitting in pipeline with nothing to promote.
        lifecycle_status=LifecycleStatus.IN_STOCK if data.vin else LifecycleStatus.PIPELINE,
        in_stock_at=utcnow() if data.vin else None,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(item)
    db.flush()

    publish(
        db,
        OutboxEvent(
            event_type="inventory.stock_item.added",
            tenant_id=tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="stock_item",
            aggregate_id=item.id,
            # KAN-144 — `origin` (originContractId, originRole) is added only
            # when a contract's confirmation created the item, so Sales can
            # link a manual configuration to it. Additive: a consumer that
            # ignores unknown keys is unaffected. KAN-270: `configurationId`,
            # likewise only when the item has one — vehicle keeps a
            # configuration a host took for good.
            payload={
                "stockNumber": item.stock_number,
                "vehicleLabel": item.vehicle_label,
                **({"configurationId": str(item.configuration_id)} if item.configuration_id is not None else {}),
                **(origin or {}),
            },
        ),
    )
    return item


def mark_purchased_if_ready(db: Session, item: StockItem) -> bool:
    """WP-7 PR-5 (ADR-052): the dealership cannot invoice a vehicle it has
    not bought (Anto, 2026-10-04 — the gate is at invoicing, not at contract
    confirmation; KAN-100). A stock item becomes invoiceable the moment
    BOTH facts are true — VIN known (lifecycle_status=in_stock) and the
    purchase is booked (purchase_price set) — whichever of the two
    completes second. Called from both promote_and_flush (PR-2) and
    record_purchase (PR-3); since KAN-446 a pipeline item's purchase promotes
    it first, so record_purchase is the one that flips it. Idempotent: a no-op past
    the first time. Returns True iff it just flipped is_invoiceable and
    emitted the event — callers decide whether to commit.
    """

    if item.is_invoiceable:
        return False
    if item.lifecycle_status != LifecycleStatus.IN_STOCK or item.purchase_price is None:
        return False

    item.is_invoiceable = True
    db.flush()
    publish(
        db,
        OutboxEvent(
            event_type="inventory.stock_item.purchased",
            tenant_id=item.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="stock_item",
            aggregate_id=item.id,
            payload={"stockNumber": item.stock_number},
        ),
    )
    return True


def create_stock_item(
    db: Session, *, tenant_id: uuid.UUID, data: StockItemCreate, actor_id: uuid.UUID | None
) -> StockItem:
    configuration_label = None
    if data.configuration_id is not None:
        # FR-C-13: both modes are legitimate here — `build` for a factory
        # order, `record` for a car being bought in. Keeping `record` is what
        # makes the offer workspace's `build`-only rule safe: a used car
        # bought but not yet received is this pipeline item, which Path A
        # finds. 404 for another tenant's configuration.
        configuration = get_configuration_for_host(db, tenant_id=tenant_id, configuration_id=data.configuration_id)
        configuration.require_mode(
            host="stock pipeline", allowed=(CONFIGURATION_MODE_BUILD, CONFIGURATION_MODE_RECORD)
        )
        # KAN-463: the configuration is the car's technical data, so one
        # that names another car is refused, as on «Konfiguration zuordnen».
        if configuration_vin_differs(configuration.vin, data.vin):
            raise configuration_vin_differs_error()
        configuration_label = configuration.label
    if data.vin is not None:
        _check_vin_not_in_stock(db, tenant_id=tenant_id, vin=data.vin)
    if data.configuration_id is not None:
        # KAN-509: after the VIN check, so the same car posted twice is still
        # refused as `vin_already_in_stock`, the refusal that names it; both
        # before vehicle-mdm is touched.
        _lock_configuration(db, tenant_id=tenant_id, configuration_id=data.configuration_id)
        _check_configuration_not_held(db, tenant_id=tenant_id, configuration_id=data.configuration_id)
    if data.vin is not None:
        # KAN-244 (Anto, option C): the item's vehicle is the one its VIN
        # resolves to, as on promotion — a client's vehicleId is ignored.
        # Resolved on vehicle-mdm's own session, before any write of ours, so
        # its commit (or its rollback on a lost race, KAN-257) never touches
        # this transaction. A refusal below leaves the vehicle-mdm record in
        # place: it is the car's global record (FR-V-15), whoever holds it.
        vehicle = resolve_vehicle_mdm_by_vin(db, vin=data.vin, actor_id=actor_id)
        data = data.model_copy(update={"vehicle_id": vehicle.id, "vin": vehicle.vin})
    try:
        item = _build_and_flush_stock_item(
            db, tenant_id=tenant_id, data=data, actor_id=actor_id, pipeline_ref=None,
            configuration_label=configuration_label,
        )
    except IntegrityError:
        # KAN-111: another writer took this VIN into stock after the check.
        db.rollback()
        held_by = _item_in_stock_with_vin(db, tenant_id=tenant_id, vin=data.vin) if data.vin is not None else None
        if held_by is None:
            raise
        raise _vin_already_in_stock(held_by) from None
    db.commit()
    db.refresh(item)
    return item


def update_stock_item(
    db: Session, *, item: StockItem, data: StockItemUpdate, actor_id: uuid.UUID | None
) -> StockItem:
    changes = data.model_dump(exclude_unset=True)
    # KAN-432 (rule 4): a condition sent on the PATCH is the same fact as
    # `change_condition`'s, so it publishes the same event, in this
    # transaction — consumers never miss a change made by either route.
    previous_condition = item.condition
    for field, value in changes.items():
        setattr(item, field, value)
    item.updated_by = actor_id
    item.version += 1
    db.flush()
    _publish_condition_changed(db, item=item, previous=previous_condition)
    db.commit()
    db.refresh(item)
    return item


def attach_configuration(
    db: Session, *, item: StockItem, configuration_id: uuid.UUID, actor_id: uuid.UUID | None
) -> StockItem:
    """KAN-463 — «Konfiguration zuordnen»: link the configuration the
    configurator produced to an item that has none (one the retired
    hand-typed intake created), so the car gets its technical data. Both
    modes, as on the pipeline intake (FR-C-13); 404 for another tenant's
    configuration. Refused when the item already has one (409
    `configuration_already_linked`: a configuration is replaced by editing
    it, never by linking another) and when both carry a VIN and they differ
    (422 `configuration_vin_differs`: it would describe another car), and
    when another stock item holds it (409 `configuration_held_by_stock_item`,
    KAN-509). Allowed on an item that has left stock too: its record keeps the car's
    data. Audited; the caller has checked If-Match."""

    if item.configuration_id is not None:
        raise ConflictError(
            "This stock item already has a configuration.",
            details={"reason": "configuration_already_linked", "configurationId": str(item.configuration_id)},
        )
    configuration = get_configuration_for_host(db, tenant_id=item.tenant_id, configuration_id=configuration_id)
    configuration.require_mode(host="stock pipeline", allowed=(CONFIGURATION_MODE_BUILD, CONFIGURATION_MODE_RECORD))
    if configuration_vin_differs(configuration.vin, item.vin):
        raise configuration_vin_differs_error()
    _lock_configuration(db, tenant_id=item.tenant_id, configuration_id=configuration.id)
    _check_configuration_not_held(db, tenant_id=item.tenant_id, configuration_id=configuration.id)
    item.configuration_id = configuration.id
    # Rule 2's three columns: the label is a copy of the configuration's,
    # refreshed now.
    item.configuration_label = configuration.label
    item.configuration_label_refreshed_at = utcnow()
    item.updated_by = actor_id
    item.version += 1
    db.flush()
    record_audit_event(
        db,
        entity_type="stock_item",
        entity_id=item.id,
        tenant_id=item.tenant_id,
        action="configuration_attached",
        actor_id=actor_id,
        before={"configurationId": None},
        after={"configurationId": str(configuration.id), "configurationLabel": configuration.label},
    )
    # KAN-270: the fact vehicle keeps a configuration a host took for good by.
    publish(
        db,
        OutboxEvent(
            event_type="inventory.stock_item.configuration_attached",
            tenant_id=item.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="stock_item",
            aggregate_id=item.id,
            payload={"stockNumber": item.stock_number, "configurationId": str(configuration.id)},
        ),
    )
    db.commit()
    db.refresh(item)
    return item


def change_condition(
    db: Session, *, item: StockItem, condition: StockItemCondition, actor_id: uuid.UUID | None
) -> StockItem:
    previous = item.condition
    item.condition = condition
    item.updated_by = actor_id
    item.version += 1
    db.flush()
    _publish_condition_changed(db, item=item, previous=previous)
    db.commit()
    db.refresh(item)
    return item


def _publish_condition_changed(db: Session, *, item: StockItem, previous: StockItemCondition) -> None:
    """The one writer of `inventory.stock_item.condition_changed`, for both
    routes that change a condition; the same value again is no change."""

    if previous == item.condition:
        return
    publish(
        db,
        OutboxEvent(
            event_type="inventory.stock_item.condition_changed",
            tenant_id=item.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="stock_item",
            aggregate_id=item.id,
            payload={"previousCondition": previous.value, "condition": item.condition.value},
        ),
    )


def list_stock_items(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    q: str | None,
    lifecycle_status: LifecycleStatus | None,
    condition: StockItemCondition | None = None,
    lifecycle_status_not: LifecycleStatus | None = None,
    condition_not: StockItemCondition | None = None,
    params: SortPageParams,
) -> tuple[list[StockItem], str | None, int, bool]:
    # FR-I-12 (PR-5): a sold (invoiced) item is absent from the active
    # list — enforced here, never a 4th lifecycle_status value. Still
    # directly fetchable by id (get_stock_item_or_404 is unaffected) for
    # whoever holds a historical link to it.
    stmt = select(StockItem).where(StockItem.tenant_id == tenant_id, StockItem.left_stock_at.is_(None))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            (StockItem.stock_number.ilike(like))
            | (StockItem.vin.ilike(like))
            | (StockItem.vehicle_label.ilike(like))
        )
    if lifecycle_status is not None:
        stmt = stmt.where(StockItem.lifecycle_status == lifecycle_status)
    if condition is not None:
        stmt = stmt.where(StockItem.condition == condition)
    # KAN-476: «ist nicht» — both columns are NOT NULL, so `!=` loses no row.
    if lifecycle_status_not is not None:
        stmt = stmt.where(StockItem.lifecycle_status != lifecycle_status_not)
    if condition_not is not None:
        stmt = stmt.where(StockItem.condition != condition_not)

    total, total_is_estimate = count_capped(db, stmt, threshold=get_settings().count_exact_threshold)
    stmt = paginate_query_sorted(stmt, model=StockItem, params=params)
    rows = list(db.scalars(stmt).all())
    items, next_cursor = build_sorted_page(rows, params)
    return items, next_cursor, total, total_is_estimate
