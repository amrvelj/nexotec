"""SalesContract service layer. Creation (PR-1) and confirmation/lifecycle
(PR-6: pending -> confirmed, the reservation call via a dedicated
session — ADR-047 Pattern B — and the two distinct events) live together
here.
"""

import copy
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.outbox import OutboxEvent, publish
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.customer.public import CustomerLifecycleStatus, get_customer_or_404, has_usable_domicile_address
from app.db import SessionLocal
from app.inventory.public import release, reserve_for_contract
from app.sales.models.contract import ContractStatus, FinancingKind, SalesContract
from app.sales.models.line_item import SalesLineItem
from app.sales.models.offer import SalesOffer
from app.sales.schemas.contract import ContractUpdate
from app.sales.services import ownership
from app.sales.services.contract_completeness import is_priced_without_inputs, require_complete
from app.sales.services.deal_editing import apply_vehicle_and_pricing_changes
from app.sales.services.deal_projection import upsert_deal_projection
from app.sales.services.numbering import allocate_contract_number
from app.sales.services.offer import apply_customer_change, resolve_customer_label
from app.sales.services.priced_deal import require_editable, require_pending
from app.valuation.public import consume_valuation_for_contract, revert_valuation_use

_EVENT_PRODUCER = "sales"

logger = logging.getLogger(__name__)


def get_contract_or_404(
    db: Session, tenant_id: uuid.UUID, contract_id: uuid.UUID, *, for_update: bool = False
) -> SalesContract:
    """`for_update` (KAN-288) locks the row until the caller commits: every
    route that changes a contract loads it this way, so an edit and a
    confirmation of the same contract run one after the other. Without it,
    an edit that read the contract while pending could write after it was
    signed (the If-Match check compares the version as read, not as
    written) and rewrite a frozen price behind the confirmed event. The
    lock is Postgres's row lock, taken in the request's own session; the
    cross-context calls confirmation makes run on sessions of their own and
    never touch this row."""

    stmt = select(SalesContract).where(SalesContract.id == contract_id, SalesContract.tenant_id == tenant_id)
    if for_update:
        stmt = stmt.with_for_update()
    contract = db.scalar(stmt)
    if contract is None:
        raise NotFoundError(f"Contract {contract_id} was not found.")
    return contract


def create_contract(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    offer: SalesOffer | None,
    actor_id: uuid.UUID | None,
    customer_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
    stock_item_id: uuid.UUID | None = None,
    vehicle_label: str | None = None,
) -> SalesContract:
    """`offer=None, customer_id=None` — a contract with neither — is refused
    at the API since KAN-276 (Anto, 2026-10-11: "no empty contracts";
    `ContractCreate`'s validator); tests still build one here to set up a
    pending contract with no buyer, the state a contract from a customer-less
    offer is in. `offer` set is "Vertrag erzeugen" from an existing
    offer's row menu, which denormalizes the offer's number as lineage and
    copies its working fields across — the confirmed reference prototype's
    own "C-001195 ← O-003216" header.

    `customer_id` set (KAN-58) is the customer→contract entry point — "New
    contract" on the customer's own row menu / 360 overflow / offers-and-
    contracts tab: a direct contract with no offer and no vehicle, just a
    known customer. Mutually exclusive with `offer` (`ContractCreate`'s own
    validator enforces this; not re-checked here) and requires `group_id`
    to resolve the customer. Only `do_not_contact` refuses at this point
    (ADR-065/FR-21 — the same attach-time guard `update_offer` uses for
    offers); a credit block and D-20's missing-address gate stay
    confirm-time-only refusals (KAN-55), because creating, like quoting,
    commits nobody.

    `stock_item_id` set (KAN-307, Anto 2026-10-12) is "Vertrag erstellen"
    on a stock car: the contract starts with that car, applied through the
    same Path A edit as the contract's own PATCH (`deal_editing`), in this
    transaction, so a car that cannot be read (404) leaves no contract
    behind. The buyer is then picked inside the pending contract
    (`ContractUpdate.customer_id`), as on an offer; confirmation refuses a
    contract without one.
    Mutually exclusive with `offer` and `customer_id` (`ContractCreate`).
    """

    customer = None
    if customer_id is not None:
        if group_id is None:
            raise ValueError("create_contract(customer_id=...) requires group_id.")
        customer = get_customer_or_404(db, group_id, customer_id)
        if customer.lifecycle_status == CustomerLifecycleStatus.DO_NOT_CONTACT:
            raise ConflictError(
                f"Customer {customer.customer_number} is do-not-contact — cannot be attached to a contract."
            )

    if offer is not None:
        # KAN-147 — one live contract per offer: a second one would sell the
        # same deal twice, and for a legacy sale's synthesised offer it would
        # hand a sale the old system invoiced to invoicing again. The offer's
        # row lock serialises two concurrent creations; a cancelled contract
        # frees the offer for a new one. Re-read under the lock, so the
        # contract copies the offer as committed (a customer merge since the
        # caller loaded it included), not the caller's stale object. The flush
        # first keeps a caller's own pending change to the offer, which a
        # refresh would otherwise discard.
        db.flush()
        db.refresh(offer, with_for_update=True)
        live = db.scalar(
            select(SalesContract.contract_number).where(
                SalesContract.tenant_id == tenant_id,
                SalesContract.offer_id == offer.id,
                SalesContract.status != ContractStatus.CANCELLED,
            ).limit(1)
        )
        if live is not None:
            raise ConflictError(
                f"Offer {offer.offer_number} already has contract {live}; only a cancelled contract frees an offer.",
                details={"reason": "offer_already_contracted", "contractNumber": live},
            )

    contract = SalesContract(
        tenant_id=tenant_id,
        contract_number=allocate_contract_number(db, tenant_id),
        offer_id=offer.id if offer is not None else None,
        offer_number=offer.offer_number if offer is not None else None,
        customer_id=offer.customer_id if offer is not None else (customer.id if customer is not None else None),
        customer_label=(
            offer.customer_label
            if offer is not None
            else resolve_customer_label(customer)
            if customer is not None
            else None
        ),
        customer_locality=offer.customer_locality if offer is not None else None,
        customer_denorm_refreshed_at=(
            offer.customer_denorm_refreshed_at
            if offer is not None
            else utcnow()
            if customer is not None
            else None
        ),
        customer_language=(
            offer.customer_language
            if offer is not None
            else customer.language.value
            if customer is not None
            else None
        ),
        vehicle_source=offer.vehicle_source if offer is not None else None,
        stock_item_id=offer.stock_item_id if offer is not None else None,
        vehicle_label=offer.vehicle_label if offer is not None else None,
        manual_vehicle_condition=offer.manual_vehicle_condition if offer is not None else None,
        # KAN-67 (ADR-083) — the pricing inputs travel with the figures, so
        # the contract can be edited while pending through the same build-up.
        manual_base_price=offer.manual_base_price if offer is not None else None,
        vehicle_snapshot=copy.deepcopy(offer.vehicle_snapshot) if offer is not None else None,
        vehicle_snapshot_frozen_at=offer.vehicle_snapshot_frozen_at if offer is not None else None,
        discount_type=offer.discount_type if offer is not None else None,
        discount_value=offer.discount_value if offer is not None else None,
        total_before_discount=offer.total_before_discount if offer is not None else None,
        cost_basis=offer.cost_basis if offer is not None else None,
        configuration_id=offer.configuration_id if offer is not None else None,
        configuration_label=offer.configuration_label if offer is not None else None,
        configuration_label_refreshed_at=offer.configuration_label_refreshed_at if offer is not None else None,
        base_price=offer.base_price if offer is not None else None,
        options_total=offer.options_total if offer is not None else None,
        list_price=offer.list_price if offer is not None else None,
        accessories_total=offer.accessories_total if offer is not None else None,
        discount_amount=offer.discount_amount if offer is not None else None,
        gross_price=offer.gross_price if offer is not None else None,
        margin=offer.margin if offer is not None else None,
        trade_in_vehicle_id=offer.trade_in_vehicle_id if offer is not None else None,
        trade_in_label=offer.trade_in_label if offer is not None else None,
        trade_in_vin=offer.trade_in_vin if offer is not None else None,
        trade_in_configuration_id=offer.trade_in_configuration_id if offer is not None else None,
        trade_in_configuration_label_refreshed_at=(
            offer.trade_in_configuration_label_refreshed_at if offer is not None else None
        ),
        trade_in_valuation_id=offer.trade_in_valuation_id if offer is not None else None,
        trade_in_value=offer.trade_in_value if offer is not None else None,
        trade_in_purchase_price=offer.trade_in_purchase_price if offer is not None else None,
        payable=offer.payable if offer is not None else None,
        financing=(
            FinancingKind.LEASING
            if offer is not None and offer.leasing_term_months is not None
            else FinancingKind.CASH
        ),
        created_by=actor_id,
        updated_by=actor_id,
        # KAN-200: the responsible seller travels from the offer while they
        # may still hold an open deal; a contract started directly — or from
        # an offer whose seller has left — belongs to whoever started it.
        **(
            ownership.inherited_owner(db, tenant_id=tenant_id, offer=offer, actor_id=actor_id)
            if offer is not None
            else ownership.default_owner(db, tenant_id=tenant_id, actor_id=actor_id)
        ),
    )
    db.add(contract)
    db.flush()
    if offer is not None:
        _copy_line_items(db, offer=offer, contract=contract)
    if stock_item_id is not None:
        apply_vehicle_and_pricing_changes(
            db,
            deal=contract,
            changes={"vehicle_source": "stock", "stock_item_id": stock_item_id, "vehicle_label": vehicle_label},
            actor_id=actor_id,
            host="contract",
        )

    publish(
        db,
        OutboxEvent(
            event_type="sales.contract.created",
            tenant_id=tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="sales_contract",
            aggregate_id=contract.id,
            payload={
                "contractNumber": contract.contract_number,
                "offerId": str(offer.id) if offer is not None else None,
                "customerId": str(contract.customer_id) if contract.customer_id is not None else None,
            },
        ),
    )
    upsert_deal_projection(db, contract=contract)
    db.commit()
    db.refresh(contract)
    return contract


def _copy_line_items(db: Session, *, offer: SalesOffer, contract: SalesContract) -> None:
    """KAN-67 — the offer's factory-option and accessory lines, as new rows
    owned by the contract (`contract_id`), so an edit of the pending
    contract rebuilds the price from the same lines the offer priced. The
    offer keeps its own rows: it stays the record of what was quoted."""

    rows = db.scalars(
        select(SalesLineItem).where(SalesLineItem.tenant_id == offer.tenant_id, SalesLineItem.offer_id == offer.id)
    ).all()
    for row in rows:
        db.add(
            SalesLineItem(
                tenant_id=contract.tenant_id,
                contract_id=contract.id,
                kind=row.kind,
                code=row.code,
                label=row.label,
                unit_price=row.unit_price,
                quantity=row.quantity,
                included=row.included,
                discount_type=row.discount_type,
                discount_value=row.discount_value,
                discount_resolved_amount=row.discount_resolved_amount,
                discount_suppressed_reason=row.discount_suppressed_reason,
                position=row.position,
            )
        )
    db.flush()


def update_contract(
    db: Session,
    *,
    contract: SalesContract,
    group_id: uuid.UUID,
    data: ContractUpdate,
    actor_id: uuid.UUID | None,
) -> SalesContract:
    """KAN-67 (ADR-083) — completes or adjusts a PENDING contract's vehicle
    and price, through the offer's own edit code (deal_editing), so the two
    can never price the same input differently. A confirmed contract is
    frozen (PRD-Sales v2: pricing "editable before confirmation, frozen on
    confirm"), a cancelled one closed: both refuse with
    `contract_not_pending`.

    Publishes no event: nothing outside Sales acts on a pending contract,
    and the frozen figures travel in `sales.contract.confirmed`'s
    pricingSnapshot (ADR-046), exactly as an offer's autosave publishes
    none. The deal grid is re-projected in the same transaction.

    KAN-315 (Anto, 2026-10-12): a contract priced without inputs (KAN-67)
    is not editable, yet since KAN-307 it cannot be signed without a buyer —
    so one that has none may be given one. Customer only: that PATCH sets
    the buyer, bumps the version and touches no price figure; a buyer sent
    with any other field, an empty PATCH, and clearing or replacing a buyer
    it already has are still refused (`contract_priced_without_inputs`).
    """

    changes = data.model_dump(exclude_unset=True)
    if _gives_a_buyer_only(contract, changes):
        require_pending(contract)
        apply_customer_change(
            db, deal=contract, group_id=group_id, customer_id=changes["customer_id"], host="a contract"
        )
        contract.updated_by = actor_id
        contract.version += 1
        upsert_deal_projection(db, contract=contract)
        db.commit()
        db.refresh(contract)
        return contract

    require_editable(contract)
    if "customer_id" in changes:
        # KAN-307 (ADR-083 amended): the buyer, as on the offer.
        apply_customer_change(
            db, deal=contract, group_id=group_id, customer_id=changes.pop("customer_id"), host="a contract"
        )
    if "owner_user_id" in changes:
        # KAN-200: the responsible seller, as on the offer.
        ownership.apply_owner(
            contract,
            ownership.resolve_owner_change(db, tenant_id=contract.tenant_id, owner_user_id=changes.pop("owner_user_id")),
        )
    apply_vehicle_and_pricing_changes(db, deal=contract, changes=changes, actor_id=actor_id, host="contract")
    upsert_deal_projection(db, contract=contract)
    db.commit()
    db.refresh(contract)
    return contract


def _gives_a_buyer_only(contract: SalesContract, changes: dict) -> bool:
    """The one write KAN-315 allows a contract priced without inputs: a
    buyer for a contract that has none, and nothing else in the request."""

    return (
        is_priced_without_inputs(contract)
        and contract.customer_id is None
        and changes.keys() == {"customer_id"}
        and changes["customer_id"] is not None
    )


def _pricing_snapshot(contract: SalesContract) -> dict:
    """The price build-up as it stood at confirmation — the contract's own
    columns (WP-8 PR-7, copied from the offer at creation and, since
    KAN-67, rebuilt only while the contract is pending; confirmation
    freezes them), not a recomputation at publish time. A frozen snapshot
    that could drift would not be a snapshot.

    Entity-private figures — `margin`, `trade_in_purchase_price`,
    `cost_basis` — are DELIBERATELY absent (ADR-029). An event fans out to
    consumers nobody reviewed, so this is the easiest place in the system
    to leak them; `test_contract_confirmed_event.py` asserts their absence
    by name.
    """

    def _money(value: object) -> str | None:
        return str(value) if value is not None else None

    return {
        "currency": "CHF",
        "basePrice": _money(contract.base_price),
        "optionsTotal": _money(contract.options_total),
        "listPrice": _money(contract.list_price),
        "accessoriesTotal": _money(contract.accessories_total),
        "discountAmount": _money(contract.discount_amount),
        "grossPrice": _money(contract.gross_price),
        "tradeInValue": _money(contract.trade_in_value),
        "payable": _money(contract.payable),
    }


def _confirmed_event_payload(contract: SalesContract) -> dict:
    """The four keys inventory's handle_sales_contract_confirmed reads
    (WP-7, plus `tradeIn.valuationId` since KAN-101) — "existing"/"manual" (not "stock"/"manual", SalesContract's own
    vocabulary) is the one translation this function exists to make —
    PLUS the frozen `pricingSnapshot` WP-8's exit criterion requires
    (ADR-046, additive) — the inventory consumer ignores `pricingSnapshot`;
    the WP-9 invoice leg is its second reader.
    """

    manual_configuration = None
    if contract.vehicle_source == "manual":
        manual_configuration = {"vehicleLabel": contract.vehicle_label, "condition": contract.manual_vehicle_condition}
        if contract.configuration_id is not None:
            # C-F (KAN-10, FR-C-12) — additive: inventory carries it onto
            # the pipeline stock item it creates. No vehicle-mdm record is
            # written for it (ADR-070).
            manual_configuration["configurationId"] = str(contract.configuration_id)

    trade_in = None
    if contract.trade_in_vehicle_id is not None or contract.trade_in_configuration_id is not None:
        # Trade-ins are always a used car by definition — there is no
        # separate condition concept on the trade-in side to carry here.
        # C-F (KAN-10): a trade-in captured through the valuation path has a
        # configuration and no vehicle-mdm record; it becomes a pipeline
        # item all the same (S-D11).
        trade_in = {"vehicleLabel": contract.trade_in_label, "condition": "used"}
        if contract.trade_in_configuration_id is not None:
            trade_in["configurationId"] = str(contract.trade_in_configuration_id)
        if contract.trade_in_valuation_id is not None:
            # KAN-101 — inventory copies this valuation's pointer onto the
            # trade-in's pipeline stock item when it creates it.
            trade_in["valuationId"] = str(contract.trade_in_valuation_id)

    return {
        "contractId": str(contract.id),
        "vehicleSource": "existing" if contract.vehicle_source == "stock" else "manual",
        "manualConfiguration": manual_configuration,
        "tradeIn": trade_in,
        "pricingSnapshot": _pricing_snapshot(contract),
    }


def confirm_contract(
    db: Session,
    *,
    contract: SalesContract,
    group_id: uuid.UUID,
    actor_id: uuid.UUID,
    session_factory: Callable[[], Session] = SessionLocal,
) -> SalesContract:
    """The core of PR-6. Guards (ADR-065/S-D19), then — for a "stock"
    vehicle source only — reserve_for_contract() on a DEDICATED SHORT-LIVED
    SESSION (ADR-047 Pattern B): it ends in its own commit, and passing the
    request session while holding this function's own uncommitted writes
    would sweep them in on the ordinary path and silently violate the rule
    on any other. If this function's own transaction then fails, the
    reservation is released as a compensating action (never rolled back
    together with it — that would be the shared-transaction anti-pattern
    ADR-047 exists to forbid).

    A trade-in valuation is consumed first (KAN-101), on its own short-lived
    session for the same reason, before the reservation. Several contracts
    may carry one valuation (Anto, 2026-09-29), so an already-used one is
    accepted; one past its validity refuses the confirmation before anything
    is reserved. If the reservation is then refused, "used" is reverted as below.
    If this function's own transaction fails, "used" is reverted only when
    this call set it and no other signed contract carries the valuation.
    Cancelling a signed contract never reverts it (ADR-066).

    `session_factory` defaults to the real `app.db.SessionLocal` (bound to
    the app's own configured database) — overridden only by tests, which
    run against a separate test-only engine and therefore need their own
    session factory rather than the production one.
    """

    if contract.status != ContractStatus.PENDING:
        raise ConflictError(
            f"Contract {contract.contract_number} cannot be confirmed from status '{contract.status.value}'.",
            details={"reason": "bad_status", "status": contract.status.value},
        )

    # One refusal path, ordered prohibitions-then-missing-field: a customer
    # the dealership may not contact, then one it may not extend credit to,
    # then one whose address it does not have (D-20 / KAN-55). Each carries a
    # machine-readable `details.reason` so the UI can localise the refusal —
    # the messages themselves are English (there is no backend i18n layer).
    if contract.customer_id is not None:
        customer = get_customer_or_404(db, group_id, contract.customer_id)
        if customer.lifecycle_status == CustomerLifecycleStatus.DO_NOT_CONTACT:
            raise ConflictError(
                f"Customer is do-not-contact — contract {contract.contract_number} cannot be confirmed.",
                details={"reason": "do_not_contact"},
            )
        if customer.credit_block:
            raise ConflictError(
                f"Customer has a credit block ({customer.credit_block_reason}) — contract "
                f"{contract.contract_number} cannot be confirmed.",
                details={"reason": "credit_block", "creditBlockReason": customer.credit_block_reason},
            )
        if not has_usable_domicile_address(db, customer_id=customer.id):
            # D-20 (ruled 2026-09-07): the address stays optional at
            # creation and is gated here instead — the one moment it is
            # genuinely needed. Same shape as the credit-block refusal
            # (Sales FR-S-24). An offer is never blocked. "Usable" is the
            # FR-03 definition, single-sourced in customer.public.
            raise ConflictError(
                f"Customer has no usable address — contract {contract.contract_number} cannot be "
                f"confirmed. Add a current domicile address to the customer record first.",
                details={"reason": "missing_address"},
            )
    else:
        # KAN-307 (Anto, 2026-10-12): a contract is never signed without a
        # buyer. A contract started from a car, or from an offer that had
        # no customer, gets one in its own PATCH first.
        raise ConflictError(
            f"Contract {contract.contract_number} has no customer — cannot be confirmed. Choose the buyer first.",
            details={"reason": "missing_customer"},
        )

    # KAN-66 (G-67) — the customer->contract entry point (KAN-58) creates a
    # contract with no offer and therefore no vehicle or price at all; until
    # now nothing stopped that shell from being signed as-is. Mirrors the
    # offer side's own completeness gate (compute_containers: pricing
    # can never be "complete" without a vehicle first) rather than inventing
    # a new rule. Checked after the customer prohibitions/address above, for
    # the same "pointless to fix first" reason those are checked in that
    # order: a blocked or address-less customer refuses regardless of what
    # the contract is for, so that is named first when both are true.
    # KAN-67: the one definition, shared with contract document generation.
    require_complete(contract, action="confirmed")

    # Plain values for the compensating actions: after db.rollback() the
    # contract's attributes are expired, and reloading them could itself fail
    # and hide why the confirmation failed.
    ids = _ConfirmationIds(
        tenant_id=contract.tenant_id, contract_id=contract.id, valuation_id=contract.trade_in_valuation_id
    )

    # KAN-101 — the trade-in valuation is consumed BEFORE the reservation,
    # so a refused or failed valuation call never leaves a reservation to
    # undo. A retry after a compensated commit failure reuses the same key;
    # reserve_for_contract answers it from the item, so the retry reserves
    # the car again rather than replaying the released reservation (KAN-114).
    valuation_newly_used = False
    if contract.trade_in_valuation_id is not None:
        short_lived = session_factory()
        try:
            valuation_newly_used = consume_valuation_for_contract(
                short_lived,
                tenant_id=contract.tenant_id,
                valuation_id=contract.trade_in_valuation_id,
                actor_id=actor_id,
            )
        except ConflictError as exc:
            if (exc.details or {}).get("reason") == "valuation_expired":
                raise ConflictError(
                    f"The trade-in valuation of contract {contract.contract_number} has expired, so the "
                    f"contract cannot be confirmed. Cancel it and create a new contract from an offer with a "
                    f"current valuation.",
                    details={"reason": "trade_in_valuation_expired", "valuationId": str(contract.trade_in_valuation_id)},
                ) from exc
            raise
        finally:
            short_lived.close()

    reservation_id: uuid.UUID | None = None
    if contract.vehicle_source == "stock" and contract.stock_item_id is not None:
        short_lived = session_factory()
        try:
            result = reserve_for_contract(
                short_lived,
                tenant_id=contract.tenant_id,
                stock_item_id=contract.stock_item_id,
                contract_id=contract.id,
                idempotency_key=f"sales.contract.confirm:{contract.id}",
            )
        except Exception:
            # End both transactions first (KAN-192). The refused reservation
            # may still hold the stock item's row lock, and this request
            # holds the contract's, which the compensation takes on its own
            # session: kept, either one could leave the compensation waiting
            # on this request — directly, or through a second confirmation
            # of the same contract queued on the contract and then on the
            # item — and Postgres cannot see that wait to break it.
            short_lived.close()
            db.rollback()
            _compensate_confirmation(
                ids, reservation_id=None, valuation_newly_used=valuation_newly_used,
                actor_id=actor_id, session_factory=session_factory,
            )
            raise
        finally:
            short_lived.close()
        reservation_id = uuid.UUID(result["reservationId"])

    try:
        contract.status = ContractStatus.CONFIRMED
        contract.signed_at = utcnow()
        contract.reservation_id = reservation_id
        contract.updated_by = actor_id
        contract.version += 1
        db.flush()

        publish(
            db,
            OutboxEvent(
                event_type="sales.contract.confirmed",
                tenant_id=contract.tenant_id,
                producer=_EVENT_PRODUCER,
                aggregate_type="sales_contract",
                aggregate_id=contract.id,
                payload=_confirmed_event_payload(contract),
            ),
        )
        upsert_deal_projection(db, contract=contract)
        db.commit()
    except Exception:
        db.rollback()
        _compensate_confirmation(
            ids, reservation_id=reservation_id, valuation_newly_used=valuation_newly_used,
            actor_id=actor_id, session_factory=session_factory,
        )
        raise

    db.refresh(contract)
    return contract


@dataclass(frozen=True)
class _ConfirmationIds:
    tenant_id: uuid.UUID
    contract_id: uuid.UUID
    valuation_id: uuid.UUID | None


def _compensate_confirmation(
    ids: _ConfirmationIds,
    *,
    reservation_id: uuid.UUID | None,
    valuation_newly_used: bool,
    actor_id: uuid.UUID,
    session_factory: Callable[[], Session],
) -> None:
    """ADR-047's compensating actions for a confirmation that did not
    complete: release the reservation, and revert the trade-in valuation's
    "used" when this confirmation set it and no other signed contract
    carries it (a signed contract stays signed on it even if cancelled —
    ADR-066). Each on its own short-lived session, like the calls they undo.

    Called from an `except` block, which re-raises the original error: each
    action is attempted even if the other fails, and a failure here is
    logged rather than raised, so it never masks why the confirmation
    failed. ADR-047 leaves what stays undone to the nightly run: a revert
    that stays undone is reported by the valuation job's "used valuation
    with no signed contract carrying it" (KAN-115); a release that stays
    undone is freed by Stock's orphan sweep (KAN-122) on its first run once
    the reservation is an hour old.

    KAN-192: by the time this runs, the confirmation's own transaction has
    ended, and with it the contract's row lock. A second confirmation of the
    same contract may have taken the lock meanwhile and committed — with
    the very reservation this one made (reserve_for_contract is idempotent
    by item and contract) and the valuation already stamped. So both
    actions run under the contract's row lock, taken here on a session of
    its own, and neither runs once the contract is signed: what a committed
    confirmation points at is never undone.
    """

    guard = session_factory()
    try:
        try:
            signed_at = guard.scalar(
                select(SalesContract.signed_at)
                .where(SalesContract.tenant_id == ids.tenant_id, SalesContract.id == ids.contract_id)
                .with_for_update()
            )
        except Exception:
            # Without the lock this cannot tell whether a concurrent
            # confirmation committed, so it undoes nothing: the orphan sweep
            # and the nightly report pick up what stays done (as above).
            logger.exception(
                "contract_confirm_compensation_failed",
                extra={"contractId": str(ids.contract_id), "action": "lock_contract"},
            )
            return
        if signed_at is not None:
            logger.warning(
                "contract_confirm_compensation_skipped",
                extra={"contractId": str(ids.contract_id), "reason": "confirmed_concurrently"},
            )
            return
        try:
            _run_compensating_actions(
                ids, reservation_id=reservation_id, valuation_newly_used=valuation_newly_used,
                actor_id=actor_id, session_factory=session_factory,
            )
        except Exception:
            logger.exception(
                "contract_confirm_compensation_failed",
                extra={"contractId": str(ids.contract_id), "action": "compensate"},
            )
    finally:
        guard.close()


def _run_compensating_actions(
    ids: _ConfirmationIds,
    *,
    reservation_id: uuid.UUID | None,
    valuation_newly_used: bool,
    actor_id: uuid.UUID,
    session_factory: Callable[[], Session],
) -> None:
    if reservation_id is not None:
        compensating = session_factory()
        try:
            release(
                compensating,
                tenant_id=ids.tenant_id,
                reservation_id=reservation_id,
                # Per reservation, not per contract: a retried confirmation
                # makes a new reservation, and its own compensation must not
                # collide with an earlier attempt's key (KAN-114).
                idempotency_key=f"sales.contract.confirm-compensate:{ids.contract_id}:{reservation_id}",
            )
        except Exception:
            logger.exception(
                "contract_confirm_compensation_failed",
                extra={"contractId": str(ids.contract_id), "action": "release_reservation"},
            )
        finally:
            compensating.close()

    if valuation_newly_used and ids.valuation_id is not None:
        compensating = session_factory()
        try:
            signed_elsewhere = compensating.scalar(
                select(SalesContract.id)
                .where(
                    SalesContract.tenant_id == ids.tenant_id,
                    SalesContract.trade_in_valuation_id == ids.valuation_id,
                    SalesContract.id != ids.contract_id,
                    SalesContract.signed_at.is_not(None),
                )
                .limit(1)
            )
            if signed_elsewhere is None:
                revert_valuation_use(
                    compensating, tenant_id=ids.tenant_id, valuation_id=ids.valuation_id, actor_id=actor_id
                )
        except Exception:
            logger.exception(
                "contract_confirm_compensation_failed",
                extra={"contractId": str(ids.contract_id), "action": "revert_valuation_use"},
            )
        finally:
            compensating.close()


def request_invoice(db: Session, *, contract: SalesContract, actor_id: uuid.UUID | None) -> SalesContract:
    """ADR-046 — a genuinely distinct event from `sales.contract.confirmed`,
    never the same name for both moments. Emitted at hand-off, not at
    signature; does not change the contract's status (INVOICED is
    finance's own trigger, WP-9+).

    The purchase gate (Anto, 2026-10-04; ADR-052, FR-I-12): the dealership
    cannot invoice a vehicle it has not bought. Confirmation is NOT gated —
    a contract may be signed and the car reserved before the purchase
    (PRD-Stock K-12). `is_invoiceable` is derived from the local replica
    in the query that loaded the contract — no call to Stock. A manually
    configured vehicle is refused too: confirmation makes it a pipeline
    stock item Sales is not told about, so Sales cannot see its purchase.

    A contract migrated from the legacy `transaction` table is refused
    whatever its purchase (Anto, 2026-10-10; KAN-147, ADR-050): the old
    system invoiced it, and a second invoice would break the gapless
    numbering's meaning (ADR-032). It stays CONFIRMED as migrated.
    """

    if contract.status != ContractStatus.CONFIRMED:
        raise ConflictError(
            f"Contract {contract.contract_number} cannot request invoicing from status "
            f"'{contract.status.value}'."
        )
    if contract.legacy_transaction_id is not None:
        raise ConflictError(
            f"Contract {contract.contract_number} was migrated from the legacy system, which invoiced it — "
            f"it cannot be invoiced again.",
            details={"reason": "legacy_sale"},
        )
    db.refresh(contract, ["is_invoiceable"])  # as of now, not as of when the caller loaded it
    if not contract.is_invoiceable:
        raise ConflictError(
            f"The vehicle of contract {contract.contract_number} has not been purchased yet — it cannot be "
            f"invoiced until Stock has booked its purchase.",
            details={"reason": "vehicle_not_purchased"},
        )

    publish(
        db,
        OutboxEvent(
            event_type="sales.contract.invoice_requested",
            tenant_id=contract.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="sales_contract",
            aggregate_id=contract.id,
            payload={
                "contractNumber": contract.contract_number,
                "stockItemId": str(contract.stock_item_id) if contract.stock_item_id else None,
                "grossPrice": str(contract.gross_price) if contract.gross_price is not None else None,
                "deliveryDate": contract.delivery_date.isoformat() if contract.delivery_date else None,
            },
        ),
    )
    db.commit()
    db.refresh(contract)
    return contract


def cancel_contract(
    db: Session,
    *,
    contract: SalesContract,
    reason: str,
    actor_id: uuid.UUID | None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> SalesContract:
    """PENDING or CONFIRMED can both be cancelled — CONFIRMED additionally
    releases the stock reservation first (Pattern B, dedicated session,
    same reasoning as confirm_contract's own reserve_for_contract() call).
    A contract migrated from the legacy system is refused (KAN-147).
    A manual configuration has no reservation_id here: Stock reserved its
    pipeline item itself and releases it on `sales.contract.cancelled`
    (KAN-158).
    """

    if contract.status not in (ContractStatus.PENDING, ContractStatus.CONFIRMED):
        raise ConflictError(
            f"Contract {contract.contract_number} cannot be cancelled from status '{contract.status.value}'.",
            details={"status": contract.status.value},
        )
    if contract.legacy_transaction_id is not None:
        # KAN-147 — a legacy sale is history (ADR-050): cancelling it would
        # publish a years-old sale as today's business and free its offer
        # for a second contract.
        raise ConflictError(
            f"Contract {contract.contract_number} was migrated from the legacy system — it cannot be cancelled.",
            details={"reason": "legacy_sale"},
        )

    if contract.status == ContractStatus.CONFIRMED and contract.reservation_id is not None:
        short_lived = session_factory()
        try:
            release(
                short_lived,
                tenant_id=contract.tenant_id,
                reservation_id=contract.reservation_id,
                idempotency_key=f"sales.contract.cancel:{contract.id}",
            )
        except NotFoundError:
            # KAN-114 — a contract confirmed before that fix may point at a
            # reservation its own compensation already released. Nothing is
            # left to release under that id, and Stock's cancellation
            # consumer still releases whatever this contract holds (KAN-158),
            # so the cancellation goes ahead rather than 404ing.
            logger.warning(
                "contract_cancel_reservation_already_released",
                extra={"contractId": str(contract.id), "reservationId": str(contract.reservation_id)},
            )
        finally:
            short_lived.close()

    contract.status = ContractStatus.CANCELLED
    contract.cancelled_reason = reason
    contract.updated_by = actor_id
    contract.version += 1
    db.flush()

    publish(
        db,
        OutboxEvent(
            event_type="sales.contract.cancelled",
            tenant_id=contract.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="sales_contract",
            aggregate_id=contract.id,
            payload={"contractNumber": contract.contract_number, "reason": reason},
        ),
    )
    upsert_deal_projection(db, contract=contract)
    db.commit()
    db.refresh(contract)
    return contract


def list_contracts(
    db: Session, *, tenant_id: uuid.UUID, customer_id: uuid.UUID | None = None, params: SortPageParams
) -> tuple[list[SalesContract], str | None, int, bool]:
    stmt = select(SalesContract).where(SalesContract.tenant_id == tenant_id)
    if customer_id is not None:
        stmt = stmt.where(SalesContract.customer_id == customer_id)
    total, total_is_estimate = count_capped(db, stmt, threshold=get_settings().count_exact_threshold)
    stmt = paginate_query_sorted(stmt, model=SalesContract, params=params)
    rows = list(db.scalars(stmt).all())
    items, next_cursor = build_sorted_page(rows, params)
    return items, next_cursor, total, total_is_estimate
