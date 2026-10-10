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


def require_complete(contract: SalesContract, *, action: str) -> None:
    """Raises a 409 naming `missing_vehicle` or `missing_price` in
    `details.reason`. `action` completes the English message ("confirmed",
    "printed"); the UI localises by the reason, never the message."""

    if not has_vehicle(contract):
        raise ConflictError(
            f"Contract {contract.contract_number} has no vehicle — cannot be {action}.",
            details={"reason": "missing_vehicle"},
        )
    if contract.gross_price is None:
        raise ConflictError(
            f"Contract {contract.contract_number} has no price — cannot be {action}.",
            details={"reason": "missing_price"},
        )
