"""What an offer and a pending contract have in common (KAN-67, ADR-083):
both carry a vehicle, a price build-up and a trade-in, and both are built
by the same code — services/snapshot.py, services/pricing.py,
services/line_items.py and services/trade_in.py take a `PricedDeal`, not
an offer. A contract therefore never has a second copy of the offer's
pricing rules; it has the same columns (sales_contract mirrors
sales_offer's pricing inputs) and runs the same functions.

The only things that differ by host live here: when it may be edited
(an offer while it is a draft, a contract while it is pending — confirmed
freezes it, PRD-Sales v2 "frozen on confirm"), which column its line items
hang off, and which side of the deal grid it projects to.
"""

import uuid
from typing import TypeVar

from sqlalchemy import ColumnElement
from sqlalchemy.orm import Session

from app.core.concurrency import check_version
from app.core.errors import ConflictError
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.line_item import SalesLineItem
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.schemas.offer import OfferContainerState
from app.sales.services.contract_completeness import (
    has_base_price,
    has_vehicle,
    is_priced_without_inputs,
)
from app.sales.services.deal_projection import upsert_deal_projection

PricedDeal = SalesOffer | SalesContract
# For functions that hand back the same kind of deal they were given.
DealT = TypeVar("DealT", SalesOffer, SalesContract)


def number(deal: PricedDeal) -> str:
    return deal.contract_number if isinstance(deal, SalesContract) else deal.offer_number


def lock_editable(db: Session, deal: PricedDeal, *, version_seen: int) -> None:
    """Take the deal's row lock — again, for a contract, whose routes took it
    already — and check, on the row as committed, that it is still editable
    and still at `version_seen`, the version the caller's If-Match matched.

    Two writers need it. `replace_line_items` takes it before touching a
    line item, so every writer of a deal and its lines locks the deal first
    and two saves cannot deadlock on the two rows (KAN-287). `set_trade_in`
    takes it after its foreign writes, whose commits released the lock the
    route took (KAN-309). Any change the caller has not flushed is reloaded
    away, so call it before changing the deal.
    """

    db.refresh(deal, with_for_update=True)
    require_editable(deal)
    check_version(deal.version, version_seen, entity_name=type(deal).__name__)


def require_editable(deal: PricedDeal) -> None:
    """An offer is edited while it is a draft; a contract while it is
    pending. A contract's refusal names `contract_not_pending` so the UI can
    say why (confirmed = signed and frozen; cancelled = closed), or
    `contract_priced_without_inputs` for one whose price predates its inputs
    (contract_completeness.is_priced_without_inputs)."""

    if isinstance(deal, SalesContract):
        require_pending(deal)
        if is_priced_without_inputs(deal):
            # Rebuilding its price would start from nothing (no snapshot, no
            # lines) and rewrite a figure the customer may already have
            # seen; it can still be confirmed or cancelled as it stands.
            raise ConflictError(
                f"Contract {deal.contract_number} was priced before contracts kept their pricing inputs and "
                f"cannot be edited. Confirm it as it stands, or cancel it and create a new one.",
                details={"reason": "contract_priced_without_inputs"},
            )
        return
    if deal.status != OfferStatus.DRAFT:
        raise ConflictError(f"Offer {deal.offer_number} can no longer be edited (status '{deal.status.value}').")


def require_pending(contract: SalesContract) -> None:
    """The half of `require_editable` that holds for every contract write,
    including the one write a contract priced without inputs still takes —
    being given a buyer (KAN-315)."""

    if contract.status != ContractStatus.PENDING:
        raise ConflictError(
            f"Contract {contract.contract_number} can no longer be edited (status '{contract.status.value}').",
            details={"reason": "contract_not_pending", "status": contract.status.value},
        )


def line_item_owner(deal: PricedDeal) -> dict[str, uuid.UUID]:
    """The constructor keyword a new SalesLineItem takes — exactly one of
    offer_id / contract_id is ever set (app.sales.models.line_item)."""

    return {"contract_id": deal.id} if isinstance(deal, SalesContract) else {"offer_id": deal.id}


def line_item_owner_clause(deal: PricedDeal) -> ColumnElement[bool]:
    if isinstance(deal, SalesContract):
        return SalesLineItem.contract_id == deal.id
    return SalesLineItem.offer_id == deal.id


def vehicle_condition(deal: PricedDeal) -> str | None:
    """A manual configuration's condition lives on its own column; a
    stock vehicle's lives only inside the frozen vehicle_snapshot JSON
    (ADR-041) — one place to read either, shared by the API's own
    OfferRead/ContractRead.vehicle_condition and services/line_items.py's
    used-vehicle discount-suppression rule.
    """

    if deal.vehicle_source == "manual":
        return deal.manual_vehicle_condition
    if deal.vehicle_snapshot:
        return deal.vehicle_snapshot.get("condition")
    return None


def project(db: Session, deal: PricedDeal) -> None:
    if isinstance(deal, SalesContract):
        upsert_deal_projection(db, contract=deal)
    else:
        upsert_deal_projection(db, offer=deal)


def compute_containers(deal: PricedDeal) -> list[OfferContainerState]:
    """Server-computed, never client-derived (FR-S-05) — the sticky
    footer's missing-requirements list and each container's own status
    badge both read from this one function, so they can never disagree.

    Requirement levels and the "placeholder" status for Leasing are fixed
    properties of the container itself (confirmed live against the
    reference prototype); everything else is derived from what has
    actually been filled in so far. KAN-67: a contract shows the same
    containers minus Leasing, which is an offer-workspace placeholder
    (the contract carries `financing` instead).
    """

    vehicle = has_vehicle(deal)
    # "complete" means a REAL base price (contract_completeness.has_base_price)
    # — a hint, not the confirm gate: a contract may be signed at CHF 0.00
    # (Anto, 2026-10-10). One priced without inputs is complete as it stands.
    priced = has_base_price(deal) or (isinstance(deal, SalesContract) and is_priced_without_inputs(deal))
    containers = [
        OfferContainerState(
            id="customer",
            requirement="required",
            status="complete" if deal.customer_id is not None else "not_started",
        ),
        OfferContainerState(id="vehicle", requirement="required", status="complete" if vehicle else "not_started"),
        OfferContainerState(
            id="pricing",
            requirement="required",
            status="complete" if priced else ("in_progress" if vehicle else "not_started"),
        ),
        OfferContainerState(
            id="trade_in",
            requirement="optional",
            status="complete" if deal.trade_in_vehicle_id is not None else "not_started",
        ),
    ]
    if isinstance(deal, SalesOffer):
        # S-D03 — never a real calculator in v1; "placeholder" is a
        # genuine third status, not "complete" dressed up.
        containers.append(OfferContainerState(id="leasing", requirement="optional", status="placeholder"))
    return containers
