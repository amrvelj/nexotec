"""The vehicle-and-price edit an offer draft and a pending contract share
(KAN-67, ADR-083): FR-S-05's Path A (a stock item), Path B (the
configurator, `build` mode only) and a manually described vehicle, the
seller's base price and discount — then the snapshot freeze and the price
build-up, re-run on every touch. `update_offer` and `update_contract` each
check their own host's rules (editable state, customer) and hand the rest
to this one function, so the two can never price the same input
differently.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.sales.services.priced_deal import PricedDeal
from app.sales.services.pricing import apply_build_up
from app.sales.services.snapshot import freeze_vehicle_snapshot
from app.vehicle.public import CONFIGURATION_MODE_BUILD, get_configuration_for_host


def apply_vehicle_and_pricing_changes(
    db: Session, *, deal: PricedDeal, changes: dict[str, Any], actor_id: uuid.UUID | None, host: str
) -> None:
    """`changes` is the request's `model_dump(exclude_unset=True)` with any
    host-specific keys already taken out; every remaining key is a plain
    column on both SalesOffer and SalesContract. `host` names the caller in
    a configuration-mode refusal ("offer workspace", "contract"). Bumps the
    version and flushes; does not commit — the caller owns the transaction.
    """

    if "configuration_id" in changes:
        _attach_configuration(
            db, deal=deal, configuration_id=changes.pop("configuration_id"), changes=changes, host=host
        )
    elif changes.get("vehicle_source") == "stock":
        # Path A replaces Path B — a stock car carries no configuration.
        _clear_configuration(deal)

    for field, value in changes.items():
        setattr(deal, field, value)

    deal.updated_by = actor_id
    deal.version += 1
    db.flush()

    # WP-8 PR-3 — re-derive pricing on every touch, not behind a separate
    # "build" button: FR-S-05's autosave is live, and freeze_vehicle_snapshot
    # is itself idempotent per vehicle identity, so this is cheap and safe
    # to call unconditionally once a vehicle exists.
    if freeze_vehicle_snapshot(db, deal=deal):
        db.flush()
    if deal.vehicle_snapshot is not None:
        apply_build_up(db, deal=deal)
        db.flush()


def _clear_configuration(deal: PricedDeal) -> None:
    deal.configuration_id = None
    deal.configuration_label = None
    deal.configuration_label_refreshed_at = None


def _attach_configuration(
    db: Session, *, deal: PricedDeal, configuration_id: uuid.UUID | None, changes: dict, host: str
) -> None:
    """FR-C-12 — Path B's configurator hands back a configuration id. The
    offer workspace and the contract accept **`build` mode only** (PRD
    v1.4): a used car being sold comes from stock through Path A, and a
    trade-in goes through the valuation path. A `record` configuration here
    is a 422, so the mode matrix holds at the API, not only in the overlay.

    Attaching makes the vehicle container a configuration (`manual`, no
    stock item), labels it, and prefills the base price from the
    configuration unless this PATCH sets one or the same configuration was
    already attached (the seller's own figure stands). The option and surcharge
    price lines arrive with the snapshot freeze that follows.
    """

    if configuration_id is None:
        _clear_configuration(deal)
        return
    configuration = get_configuration_for_host(db, tenant_id=deal.tenant_id, configuration_id=configuration_id)
    configuration.require_mode(host=host, allowed=(CONFIGURATION_MODE_BUILD,))
    is_new_configuration = deal.configuration_id != configuration.id
    deal.configuration_id = configuration.id
    deal.configuration_label = configuration.label
    deal.configuration_label_refreshed_at = utcnow()
    deal.vehicle_source = "manual"
    deal.stock_item_id = None
    deal.vehicle_label = configuration.label
    deal.manual_vehicle_condition = "new"
    for field in ("vehicle_source", "stock_item_id", "vehicle_label", "manual_vehicle_condition"):
        changes.pop(field, None)
    # Re-attaching the same configuration (the advisor reopened and saved
    # it) never overwrites a base price the seller set.
    if "manual_base_price" not in changes and (is_new_configuration or deal.manual_base_price is None):
        deal.manual_base_price = configuration.base_price
