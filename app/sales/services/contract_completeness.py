"""What makes a contract complete enough to sign or to print (KAN-66,
KAN-67): a vehicle and a price. One definition, read by
`contract.confirm_contract` and `document.generate_contract_document`, so
the two refusals can never disagree about what "complete" means.

Mirrors the offer side's own completeness gate (compute_containers:
pricing can never be "complete" without a vehicle first), so the vehicle is
named before the price when both are missing.
"""

from app.core.errors import ConflictError
from app.sales.models.contract import SalesContract
from app.sales.models.offer import SalesOffer


def has_vehicle(deal: SalesOffer | SalesContract) -> bool:
    """Also the vehicle container's "complete" on an offer or a contract
    (priced_deal.compute_containers) — one definition for both."""

    return deal.vehicle_source is not None and (deal.stock_item_id is not None or deal.vehicle_label is not None)


def has_base_price(deal: SalesOffer | SalesContract) -> bool:
    """A REAL price to build from, not merely `gross_price is not None`:
    build_up() always materializes a figure, including Decimal(0) for a car
    whose base price was never entered — that is not "priced" (WP-8 PR-3)."""

    return (deal.vehicle_source == "manual" and deal.manual_base_price is not None) or (
        deal.vehicle_source == "stock"
        and deal.vehicle_snapshot is not None
        and deal.vehicle_snapshot.get("basePrice") is not None
    )


def is_priced_without_inputs(contract: SalesContract) -> bool:
    """KAN-67 — a contract that carries price figures but none of the inputs
    they were built from: copied from an offer before contracts kept their
    inputs, and not backfilled because its offer has since changed
    (migration 381b0c2c67dc). Its figures stand as they are; rebuilding them
    from nothing would rewrite its price, so it is not editable."""

    return contract.vehicle_snapshot is None and contract.gross_price is not None


def has_price(contract: SalesContract) -> bool:
    """What confirmation and printing require: a price figure. CHF 0.00 —
    what a car whose base price was never entered builds to — counts
    (Anto, 2026-10-10, KAN-67: "allow CHF 0.00"); only an empty price is
    `missing_price`. The pricing container still says "in progress" for
    it (has_base_price), as a hint, not a gate."""

    return contract.gross_price is not None


def require_complete(contract: SalesContract, *, action: str) -> None:
    """Raises a 409 naming `missing_vehicle` or `missing_price` in
    `details.reason`. `action` completes the English message ("confirmed",
    "printed"); the UI localises by the reason, never the message."""

    if not has_vehicle(contract):
        raise ConflictError(
            f"Contract {contract.contract_number} has no vehicle — cannot be {action}.",
            details={"reason": "missing_vehicle"},
        )
    if not has_price(contract):
        raise ConflictError(
            f"Contract {contract.contract_number} has no price — cannot be {action}.",
            details={"reason": "missing_price"},
        )
