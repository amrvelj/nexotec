"""StockItem service layer (WP-7 PR-1)."""

import datetime as dt
import hashlib
import logging
import uuid
from collections.abc import Iterable

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.outbox import OutboxEvent, publish
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.core.validators import is_valid_vin, normalise_vin
from app.inventory.models.stock_item import (
    AgeingBucket,
    LifecycleStatus,
    StockItem,
    StockItemCondition,
    StockNumberSequence,
)
from app.inventory.schemas.stock_item import StockItemCreate, StockItemUpdate, StockItemVinCorrectionRequest
from app.inventory.services import commercial_change
from app.vehicle.public import (
    CONFIGURATION_MODE_BUILD,
    CONFIGURATION_MODE_RECORD,
    correct_vehicle_vin,
    get_configuration_for_host,
)

_EVENT_PRODUCER = "inventory"
logger = logging.getLogger(__name__)


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


def get_stock_numbers(
    db: Session, *, tenant_id: uuid.UUID, stock_item_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """KAN-157: the stock number of each of the tenant's items among
    `stock_item_ids` — plain data for Sales' label backfill. An id naming no
    item of the tenant is absent from the answer. A SELECT only."""

    ids = set(stock_item_ids)
    if not ids:
        return {}
    rows = db.execute(
        select(StockItem.id, StockItem.stock_number).where(StockItem.tenant_id == tenant_id, StockItem.id.in_(ids))
    ).all()
    return {row.id: row.stock_number for row in rows}


def is_stock_item_purchased(db: Session, *, tenant_id: uuid.UUID, stock_item_id: uuid.UUID) -> bool:
    """KAN-146: whether this dealership holds the stock item as bought now —
    `is_invoiceable`, the fact `inventory.stock_item.purchased` and
    `inventory.stock_item.storno` replicate (ADR-052). Sales' storno consumer
    re-reads it because delivery is unordered. A SELECT only; an item of
    another dealership, or none, is False."""

    return bool(
        db.scalar(
            select(StockItem.is_invoiceable).where(StockItem.id == stock_item_id, StockItem.tenant_id == tenant_id)
        )
    )


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


def _item_in_stock_with_vin(
    db: Session, *, tenant_id: uuid.UUID, vin: str, excluding: uuid.UUID | None = None
) -> StockItem | None:
    """KAN-111: the dealership's item holding this VIN that has not left
    stock — what `uq_stock_item_tenant_id_vin_in_stock` allows at most one
    of. An item that left stock (invoiced, FR-I-12) does not count; a
    storno'd one still does (Anto, 2026-10-07), and so does a pipeline item
    created with its VIN (KAN-554). `excluding` leaves out the item being
    promoted, which holds its own VIN already when it was created with one."""

    query = select(StockItem).where(
        StockItem.tenant_id == tenant_id, StockItem.vin == vin, StockItem.left_stock_at.is_(None)
    )
    if excluding is not None:
        query = query.where(StockItem.id != excluding)
    return db.scalar(query)


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


CONFIGURATION_INDEX = "uq_stock_item_tenant_id_configuration_id"


def _raise_if_configuration_taken(
    db: Session, exc: IntegrityError, *, tenant_id: uuid.UUID, configuration_id: uuid.UUID | None
) -> None:
    """KAN-513: the partial unique index `uq_stock_item_tenant_id_configuration_id`
    refused the write — the same 409 as the check under the lock."""

    if configuration_id is None or CONFIGURATION_INDEX not in str(exc.orig):
        return
    holder = db.scalar(
        select(StockItem).where(StockItem.tenant_id == tenant_id, StockItem.configuration_id == configuration_id).limit(1)
    )
    if holder is not None:
        raise configuration_held_conflict(holder) from None


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
        raise configuration_held_conflict(held_by)


def configuration_held_conflict(held_by: StockItem) -> ConflictError:
    """KAN-509/KAN-513: the one 409 for a configuration another stock item
    of the Betrieb holds — the check under the lock and the unique index."""

    return ConflictError(
        f"This configuration already belongs to stock item {held_by.stock_number}.",
        details={
            "reason": "configuration_held_by_stock_item",
            "stockItemId": str(held_by.id),
            "stockNumber": held_by.stock_number,
        },
    )


def _check_vin_not_in_stock(
    db: Session, *, tenant_id: uuid.UUID, vin: str, excluding: uuid.UUID | None = None
) -> None:
    """Refuse before writing, so the ordinary duplicate is a 409 with a
    reason. Two writers can still race past it; the unique index then
    refuses, and the callers map that to the same 409."""

    held_by = _item_in_stock_with_vin(db, tenant_id=tenant_id, vin=vin, excluding=excluding)
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
        # Every item is created without a vehicle-mdm record: a car not yet
        # bought has none (ADR-045, ADR-103; KAN-554, Anto 2026-11-05, option
        # A). Promotion at purchase booking gives it one. StockItemCreate
        # refuses a vehicleId without a VIN (KAN-324) and create_stock_item
        # drops one sent with a VIN.
        vehicle_id=data.vehicle_id,
        vin=data.vin,
        # ADR-117 (2), KAN-556: a configured car is named by its
        # configuration's label — the client's text is not read — and the
        # name follows the configuration from here on (attach, the nightly
        # refresh). An item without a configuration keeps its own text.
        vehicle_label=(
            configuration_label
            if data.configuration_id is not None and configuration_label is not None
            else data.vehicle_label
        ),
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
        # ADR-103, ADR-116 (KAN-554): every create lands in pipeline, a VIN
        # given with it included. Purchase booking (purchase.record_purchase)
        # is the only path to in_stock.
        lifecycle_status=LifecycleStatus.PIPELINE,
        in_stock_at=None,
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
            # KAN-189 (Anto, 2026-11-02, option A) — additive: the car's
            # vehicle-mdm record (promotion set it), the purchase date, who
            # booked it and, for an item a contract's confirmation created,
            # that contract and its role. The customer context records a bought
            # trade-in's customer as its former owner from these; a consumer that
            # ignores unknown keys is unaffected. KAN-632 (additive): a
            # trade-in's seller and signature (`trade_in_seller_fields`).
            payload={
                "stockNumber": item.stock_number,
                "vehicleId": str(item.vehicle_id) if item.vehicle_id else None,
                "purchaseDate": item.purchase_date.isoformat() if item.purchase_date else None,
                "purchasedBy": str(item.updated_by) if item.updated_by else None,
                **pipeline_origin(item.pipeline_ref),
                **trade_in_seller_fields(item),
            },
        ),
    )
    return True


def pipeline_origin(pipeline_ref: str | None) -> dict[str, str]:
    """The contract a contract's confirmation created this item for, read back
    from its `pipeline_ref` (`contract:<id>:manual` / `contract:<id>:trade_in`,
    services/pipeline.py) as the same `originContractId` / `originRole` keys
    `inventory.stock_item.added` carries (KAN-144). Empty for any other item."""

    if not pipeline_ref:
        return {}
    parts = pipeline_ref.split(":")
    if len(parts) != 3 or parts[0] != "contract" or parts[2] not in _PIPELINE_ORIGIN_ROLES:
        return {}
    try:
        contract_id = uuid.UUID(parts[1])
    except ValueError:
        return {}
    return {"originContractId": str(contract_id), "originRole": _PIPELINE_ORIGIN_ROLES[parts[2]]}


_PIPELINE_ORIGIN_ROLES = {"manual": "manual_configuration", "trade_in": "trade_in"}


def apply_trade_in_seller(item: StockItem, *, customer_id: str | None, signed_at: str | None) -> None:
    """KAN-632: a trade-in's pipeline item keeps who traded the car in and
    when the contract was signed, as `sales.contract.confirmed` named them —
    both or neither (a confirmation published before KAN-632 names neither)."""

    if not customer_id or not signed_at:
        return
    item.trade_in_customer_id = uuid.UUID(customer_id)
    item.trade_in_signed_at = dt.datetime.fromisoformat(signed_at)


def apply_trade_in_vin(item: StockItem, *, vin: object) -> None:
    """KAN-536: a trade-in's pipeline item keeps the VIN its contract holds,
    as `sales.contract.confirmed` named it (`tradeIn.vin`) — in the one
    spelling, and only a VIN a purchase can be booked with. A confirmation
    without one (published before KAN-536, or a contract that holds none)
    and a pre-1981 chassis number (KAN-353: never a vehicle-mdm VIN) leave
    the item without: it is then bought as before, the VIN typed and checked
    against its configuration only."""

    if not isinstance(vin, str):
        return
    normalised = normalise_vin(vin)
    if is_valid_vin(normalised):
        item.trade_in_vin = normalised


def trade_in_vin_differs(item: StockItem, *, configuration_vin: str | None, vin: str) -> bool:
    """KAN-536 (Anto, 2026-11-12, option A): the VIN typed at purchase booking
    is not the one the trade-in's contract holds. Only while the item's
    configuration carries no VIN: one that does is the car's technical data
    and decides alone (`configuration_vin_differs`, KAN-510) — it is where a
    wrong VIN is corrected («Konfiguration bearbeiten»), which the signed
    contract's cannot be, so the two never have to agree."""

    if item.trade_in_vin is None or configuration_vin is not None:
        return False
    return normalise_vin(item.trade_in_vin) != normalise_vin(vin)


def trade_in_vin_differs_error() -> UnprocessableEntityError:
    return UnprocessableEntityError(
        "The VIN is not the one the trade-in's contract holds.",
        details={"field": "vin", "reason": "trade_in_vin_differs"},
    )


def trade_in_seller_fields(item: StockItem) -> dict[str, str]:
    """KAN-632: the trade-in's seller as the item's purchase and storno events
    carry it (`tradeInCustomerId`, `tradeInSignedAt`, the same keys on both)
    — what the customer context records the former owner from. Empty for an
    item that keeps none: anything but a trade-in, or one created before
    KAN-632 (the customer context then asks through the composition root)."""

    if item.trade_in_customer_id is None or item.trade_in_signed_at is None:
        return {}
    return {
        "tradeInCustomerId": str(item.trade_in_customer_id),
        "tradeInSignedAt": item.trade_in_signed_at.isoformat(),
    }


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
        # refused as `vin_already_in_stock`, the refusal that names it.
        _lock_configuration(db, tenant_id=tenant_id, configuration_id=data.configuration_id)
        _check_configuration_not_held(db, tenant_id=tenant_id, configuration_id=data.configuration_id)
    # KAN-554 (ADR-116; Anto, 2026-11-05, option A): a VIN given on create is
    # stored (normalised and validated by StockItemCreate, KAN-244) on a
    # pipeline item, and nothing is written to vehicle-mdm — a car not yet
    # bought has no record there (ADR-045, ADR-103). Purchase booking promotes
    # it (FR-V-04) and resolves its record then. A client's vehicleId is
    # ignored, as since KAN-244: the record is found from the VIN, at promotion.
    data = data.model_copy(update={"vehicle_id": None})
    try:
        item = _build_and_flush_stock_item(
            db, tenant_id=tenant_id, data=data, actor_id=actor_id, pipeline_ref=None,
            configuration_label=configuration_label,
        )
    except IntegrityError as exc:
        # KAN-111: another writer took this VIN into stock after the check.
        db.rollback()
        held_by = _item_in_stock_with_vin(db, tenant_id=tenant_id, vin=data.vin) if data.vin is not None else None
        if held_by is not None:
            raise _vin_already_in_stock(held_by) from None
        # KAN-513: a writer outside the lock took the configuration.
        _raise_if_configuration_taken(db, exc, tenant_id=tenant_id, configuration_id=data.configuration_id)
        raise
    db.commit()
    db.refresh(item)
    return item


def update_stock_item(
    db: Session, *, item: StockItem, data: StockItemUpdate, actor_id: uuid.UUID | None
) -> StockItem:
    changes = data.model_dump(exclude_unset=True)
    # ADR-117 (2), KAN-556: a configured car's name is its configuration's
    # label and follows it; a PATCH may resend it unchanged, never set another.
    if (
        item.configuration_id is not None
        and "vehicle_label" in changes
        and changes["vehicle_label"] != item.vehicle_label
    ):
        raise UnprocessableEntityError(
            "This car's name is its configuration's label and follows it; change the configuration instead.",
            details={"reason": "vehicle_label_follows_configuration", "field": "vehicleLabel"},
        )
    # KAN-432 (rule 4): a condition sent on the PATCH is the same fact as
    # `change_condition`'s, so it publishes the same event, in this
    # transaction — consumers never miss a change made by either route.
    previous_condition = item.condition
    # KAN-553 (ADR-115): a price, colour or odometer change reaches the open
    # offers and pending contracts on this car.
    commercial_before = commercial_change.commercial_state(db, item)
    for field, value in changes.items():
        setattr(item, field, value)
    item.updated_by = actor_id
    item.version += 1
    db.flush()
    _publish_condition_changed(db, item=item, previous=previous_condition)
    commercial_change.publish_if_changed(db, item=item, before=commercial_before, actor_id=actor_id)
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
    # refreshed now — and the car's name follows it from now on (ADR-117 (2),
    # KAN-556), the text the retired hand-typed intake stored replaced.
    previous_vehicle_label = item.vehicle_label
    commercial_before = commercial_change.commercial_state(db, item)
    item.configuration_label = configuration.label
    item.configuration_label_refreshed_at = utcnow()
    item.vehicle_label = configuration.label
    item.updated_by = actor_id
    item.version += 1
    try:
        db.flush()
    except IntegrityError as exc:
        # KAN-513: a writer outside the lock took the configuration. The
        # request is refused, so its transaction is rolled back first.
        db.rollback()
        _raise_if_configuration_taken(db, exc, tenant_id=item.tenant_id, configuration_id=configuration.id)
        raise
    record_audit_event(
        db,
        entity_type="stock_item",
        entity_id=item.id,
        tenant_id=item.tenant_id,
        action="configuration_attached",
        actor_id=actor_id,
        before={"configurationId": None, "vehicleLabel": previous_vehicle_label},
        after={
            "configurationId": str(configuration.id),
            "configurationLabel": configuration.label,
            "vehicleLabel": configuration.label,
        },
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
    commercial_change.publish_if_changed(db, item=item, before=commercial_before, actor_id=actor_id)
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


def correct_vin(
    db: Session, *, item: StockItem, data: StockItemVinCorrectionRequest, actor_id: uuid.UUID | None
) -> StockItem:
    """KAN-557 «VIN korrigieren» (ADR-116 (2)-(3); Anto, 2026-11-03, on
    KAN-544: «wir lassen eine Korrektur am Lager zu»). The VIN a car in
    stock was bought under was wrong; the correction travels to every record
    that carries it — the stock item, its vehicle-mdm record and its
    configuration — so the three describe one car again.

    **Two contexts, two transactions (ADR-047).** Vehicle owns the VIN: it is
    asked first, through `vehicle.public.correct_vehicle_vin`, which rewrites
    the record and the configuration and commits on its own session. A
    refusal there leaves this item untouched. Then the item is written,
    audited `vin_corrected` and `inventory.stock_item.vin_corrected` is
    published, in this session's one commit. Should that fail, the
    compensation asks Vehicle to put the old VIN back (the same call, VINs
    swapped, audited `vin_correction_reverted`); should even that fail, the
    nightly reconciliation reports the drift (rule 10). A retry with the
    same Idempotency-Key completes it: Vehicle's step is compare-and-set and
    writes nothing a second time.

    Refused before anything is written: the car has left stock (409
    `vin_correction_left_stock`); it has no vehicle-mdm record — a pipeline
    car never bought, whose VIN is corrected in its configuration and wins at
    booking (KAN-575) — (409 `vin_correction_needs_vehicle`); the VIN is the
    one it has (422 `vin_unchanged`); another item of the Betrieb holds it in
    stock (409 `vin_already_in_stock`); the configuration names a third VIN
    (422 `configuration_vin_differs`). Vehicle's refusals pass through
    (`vin_belongs_to_other_vehicle`, `vehicle_vin_changed`, `vehicle_merged`).

    The caller loaded `item` with its row lock and checked If-Match."""

    if item.left_stock_at is not None:
        raise ConflictError(
            f"Stock item {item.stock_number} has left stock — its VIN is no longer corrected here.",
            details={"reason": "vin_correction_left_stock", "stockItemId": str(item.id)},
        )
    if item.vehicle_id is None or item.vin is None:
        raise ConflictError(
            f"Stock item {item.stock_number} has no vehicle record yet — correct the VIN in its configuration.",
            details={"reason": "vin_correction_needs_vehicle", "stockItemId": str(item.id)},
        )
    old_vin = item.vin
    new_vin = data.new_vin
    tenant_id = item.tenant_id
    configuration_id = item.configuration_id
    if new_vin == old_vin:
        raise UnprocessableEntityError(
            "The new VIN is the one the car already has.",
            details={"field": "newVin", "reason": "vin_unchanged"},
        )
    _check_vin_not_in_stock(db, tenant_id=item.tenant_id, vin=new_vin, excluding=item.id)
    if item.configuration_id is not None:
        configuration = get_configuration_for_host(db, tenant_id=item.tenant_id, configuration_id=item.configuration_id)
        if configuration.vin is not None and normalise_vin(configuration.vin) not in (old_vin, new_vin):
            raise configuration_vin_differs_error(field="newVin")

    vehicle = correct_vehicle_vin(
        db,
        vehicle_id=item.vehicle_id,
        old_vin=old_vin,
        new_vin=new_vin,
        reason=data.reason,
        actor_id=actor_id,
        tenant_id=item.tenant_id,
        configuration_id=item.configuration_id,
    )

    try:
        _write_corrected_vin(db, item=item, old_vin=old_vin, new_vin=new_vin, reason=data.reason, actor_id=actor_id)
        db.commit()
    except Exception:
        # Compensate before this transaction ends: every stock write was
        # flushed inside `_write_corrected_vin`'s savepoint, so a refused one
        # rolled back only that savepoint and the item's row lock is still
        # held — no second correction of the item slips in between. (Only a
        # failure of the COMMIT itself ends it first; reconciliation reports
        # what that leaves.) Vehicle's own session needs nothing from this one.
        if vehicle.record_changed or vehicle.configuration_changed:
            # Only what this call changed is put back: a record already
            # carrying the new VIN stays; a configuration it only re-linked
            # is re-linked, never given the old VIN.
            _compensate_vin_correction(
                db,
                vehicle_id=vehicle.vehicle_id,
                tenant_id=tenant_id,
                configuration_id=configuration_id if vehicle.configuration_changed else None,
                include_record=vehicle.record_changed,
                relink_configuration_only=not vehicle.configuration_vin_changed,
                old_vin=old_vin,
                new_vin=new_vin,
                reason=data.reason,
                actor_id=actor_id,
            )
        db.rollback()
        raise
    db.refresh(item)
    return item


def _write_corrected_vin(
    db: Session, *, item: StockItem, old_vin: str, new_vin: str, reason: str, actor_id: uuid.UUID | None
) -> None:
    """The stock half of the correction: the row, its audit row and its
    event, in the caller's transaction — all three flushed inside one
    savepoint, so a refused write rolls back only that savepoint and the
    caller's transaction (with the item's row lock) stays open for the
    compensation; the commit that follows has nothing left to flush. A
    racing writer that took the VIN into this Betrieb's stock meanwhile is
    refused by the unique index — mapped to the same 409 the pre-check
    gives."""

    payload: dict[str, object] = {
        "stockNumber": item.stock_number,
        "vehicleId": str(item.vehicle_id),
        "previousVin": old_vin,
        "vin": new_vin,
        "correctedBy": str(actor_id) if actor_id else None,
    }
    if item.configuration_id is not None:
        payload["configurationId"] = str(item.configuration_id)
    try:
        with db.begin_nested():
            item.vin = new_vin
            item.updated_by = actor_id
            item.version += 1
            db.flush()
            record_audit_event(
                db,
                entity_type="stock_item",
                entity_id=item.id,
                tenant_id=item.tenant_id,
                action="vin_corrected",
                actor_id=actor_id,
                before={"vin": old_vin},
                after={"vin": new_vin},
                reason=reason,
            )
            publish(
                db,
                OutboxEvent(
                    event_type="inventory.stock_item.vin_corrected",
                    tenant_id=item.tenant_id,
                    producer=_EVENT_PRODUCER,
                    aggregate_type="stock_item",
                    aggregate_id=item.id,
                    payload=payload,
                ),
            )
            db.flush()
    except IntegrityError:
        held_by = _item_in_stock_with_vin(db, tenant_id=item.tenant_id, vin=new_vin, excluding=item.id)
        if held_by is None:
            raise
        raise _vin_already_in_stock(held_by) from None


def _compensate_vin_correction(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    tenant_id: uuid.UUID,
    configuration_id: uuid.UUID | None,
    include_record: bool,
    relink_configuration_only: bool,
    old_vin: str,
    new_vin: str,
    reason: str,
    actor_id: uuid.UUID | None,
) -> None:
    """ADR-047's compensating action: Vehicle puts the old VIN back on what
    this correction changed — the record when `include_record`, the
    configuration when `configuration_id` is given (only its link, when
    `relink_configuration_only`) — and nothing else. The two legs share
    Vehicle's one transaction: should the configuration leg be refused (it
    names a third VIN by now), the record's revert is rolled back with it,
    logged at ERROR and reported by reconciliation. A failure here is logged at
    ERROR and left to the nightly reconciliation, which reports a stock item
    whose VIN differs from its record's or its configuration's."""

    try:
        correct_vehicle_vin(
            db,
            vehicle_id=vehicle_id,
            old_vin=new_vin,
            new_vin=old_vin,
            reason=reason,
            actor_id=actor_id,
            tenant_id=tenant_id,
            configuration_id=configuration_id,
            compensation=True,
            include_record=include_record,
            relink_configuration_only=relink_configuration_only,
        )
    except Exception:
        logger.exception(
            "VIN correction compensation failed; the nightly reconciliation will report the drift",
            extra={"vehicleId": str(vehicle_id), "tenantId": str(tenant_id)},
        )
