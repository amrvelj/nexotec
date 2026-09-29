"""KAN-101: contract confirmation consumes the trade-in valuation, and
Stock's valuation pointer is written when the trade-in becomes a pipeline
stock item.

Rulings exercised here (Anto, 2026-09-29, on the ticket):
- confirmation marks the trade-in valuation used — a call into
  app.valuation.public with its own commit, never a shared transaction
  (ADR-047);
- one valuation may back several contracts (Option B): a second contract
  carrying an already-used valuation still confirms;
- a valuation past its validity refuses confirmation; a draft does not;
- if the contract's own commit fails, "used" is reverted only when this
  confirmation set it and no other signed contract carries the valuation;
- cancelling a signed contract leaves the valuation used (ADR-066).
"""

import datetime as dt
import os
import threading
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.inventory.models.stock_item import ReservationState, StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.pipeline import handle_sales_contract_confirmed
from app.inventory.services.stock_item import create_stock_item, get_stock_item_or_404
from app.sales.models.contract import ContractStatus
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import cancel_contract, confirm_contract, create_contract
from app.sales.services.offer import create_offer, update_offer
from app.sales.services.trade_in import attach_trade_in_valuation, set_trade_in
from app.valuation.models.valuation import Valuation, ValuationSource
from app.valuation.public import consume_valuation_for_contract, get_valuation_or_404
from app.valuation.schemas.valuation import ValuationCreate
from app.valuation.services.valuation import create_valuation, derive_status, mark_used
from tests.test_sales_lifecycle_reservation import _customer, _dealership, _session_factory

_TRADE_IN_VIN = "WVWZZZ1KZAW654321"


def _valuation(db_session, tenant_id, group_id, *, source=ValuationSource.MANUAL, is_draft=False) -> Valuation:
    return create_valuation(
        db_session, tenant_id=tenant_id, group_id=group_id,
        data=ValuationCreate(
            vin=_TRADE_IN_VIN, vehicle_make="VW", vehicle_model="Golf", source=source,
            final_offer=Decimal("12000.00"), is_draft=is_draft,
        ),
        actor_id=uuid.uuid4(),
    )


_STOCK_VINS = iter(f"TMBJJ7NE0L0{n:06d}" for n in range(1, 1000))


def _trade_in_contract(db_session, dealership_id, group_id, valuation: Valuation):
    """A contract on its own stock car (a fresh VIN each call, so two deals
    never collide on stock's VIN index) whose offer carries the trade-in
    with `valuation` attached explicitly — the auto-attach in set_trade_in
    skips drafts."""

    item = create_stock_item(
        db_session, tenant_id=dealership_id,
        data=StockItemCreate(vehicle_label="Skoda Octavia 2.0 TDI", condition=StockItemCondition.USED, vin=next(_STOCK_VINS)),
        actor_id=uuid.uuid4(),
    )
    customer = _customer(db_session, group_id)
    offer = create_offer(db_session, tenant_id=dealership_id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=group_id,
        data=OfferUpdate(customer_id=customer.id, vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
        actor_id=uuid.uuid4(),
    )
    offer = set_trade_in(
        db_session, offer=offer, group_id=group_id, vin=_TRADE_IN_VIN, plate=None, canton=None,
        vehicle_label="VW Golf 1.5 TSI", customer_id=None, actor_id=uuid.uuid4(),
    )
    offer = attach_trade_in_valuation(db_session, offer=offer, valuation_id=valuation.id, actor_id=uuid.uuid4())
    contract = create_contract(db_session, tenant_id=dealership_id, offer=offer, actor_id=uuid.uuid4())
    assert contract.trade_in_valuation_id == valuation.id
    return contract, item


def _reload_valuation(db_session, valuation_id) -> Valuation:
    db_session.expire_all()
    return db_session.get(Valuation, valuation_id)


def _confirm(db_session, engine, contract, group_id):
    return confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )


def test_confirming_a_contract_marks_its_trade_in_valuation_used(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)
    assert derive_status(valuation) == "valid"

    _confirm(db_session, engine, contract, group_id)

    assert derive_status(_reload_valuation(db_session, valuation.id)) == "used"
    assert db_session.query(OutboxMessage).filter_by(
        aggregate_id=valuation.id, event_type="valuation.used"
    ).count() == 1


def test_the_confirmed_event_names_the_trade_in_valuation(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)

    _confirm(db_session, engine, contract, group_id)

    message = db_session.query(OutboxMessage).filter_by(
        aggregate_id=contract.id, event_type="sales.contract.confirmed"
    ).one()
    assert message.payload["tradeIn"] == {
        "vehicleLabel": "VW Golf 1.5 TSI", "condition": "used", "valuationId": str(valuation.id),
    }


def test_a_second_contract_with_an_already_used_valuation_still_confirms(db_session, engine):
    """Option B: one valuation may back several contracts. The same VIN may
    sit in pipeline twice; stock's own VIN index stops it twice in stock."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    first, _ = _trade_in_contract(db_session, dealership.id, group_id, valuation)
    _confirm(db_session, engine, first, group_id)

    # A second deal on a different stock car, same trade-in, same valuation.
    second, _ = _trade_in_contract(db_session, dealership.id, group_id, valuation)
    confirmed = _confirm(db_session, engine, second, group_id)

    assert confirmed.status == ContractStatus.CONFIRMED
    assert derive_status(_reload_valuation(db_session, valuation.id)) == "used"
    # "used" is a fact that happened once — the second consumption does not
    # publish it again.
    assert db_session.query(OutboxMessage).filter_by(
        aggregate_id=valuation.id, event_type="valuation.used"
    ).count() == 1


def test_confirming_twice_is_refused_by_status_not_by_the_valuation(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)
    _confirm(db_session, engine, contract, group_id)

    with pytest.raises(ConflictError) as exc:
        _confirm(db_session, engine, contract, group_id)
    assert exc.value.details["reason"] == "bad_status"
    assert derive_status(_reload_valuation(db_session, valuation.id)) == "used"


def test_an_expired_valuation_refuses_confirmation_before_anything_is_reserved(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, item = _trade_in_contract(db_session, dealership.id, group_id, valuation)
    valuation.valid_until = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    db_session.commit()
    assert derive_status(valuation) == "expired"

    with pytest.raises(ConflictError) as exc:
        _confirm(db_session, engine, contract, group_id)
    assert exc.value.details["reason"] == "trade_in_valuation_expired"

    db_session.rollback()
    db_session.expire_all()
    assert get_stock_item_or_404(db_session, dealership.id, item.id).reservation_state == ReservationState.NONE
    # The valuation is consumed before the reservation, so a refusal never
    # reserves and releases (review finding D; KAN-114's stale-cache path).
    assert db_session.query(OutboxMessage).filter_by(
        aggregate_id=item.id, event_type="inventory.stock_item.reserved"
    ).count() == 0
    assert db_session.get(type(contract), contract.id).status == ContractStatus.PENDING
    assert derive_status(_reload_valuation(db_session, valuation.id)) == "expired"


def test_a_used_valuation_past_its_validity_also_refuses(db_session, engine):
    """Use case 4 holds for the second deal too: 'used' does not hide that
    the figure is past the date the dealership stood behind it."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    first, _ = _trade_in_contract(db_session, dealership.id, group_id, valuation)
    _confirm(db_session, engine, first, group_id)
    second, _ = _trade_in_contract(db_session, dealership.id, group_id, valuation)

    stored = _reload_valuation(db_session, valuation.id)
    stored.valid_until = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    db_session.commit()

    with pytest.raises(ConflictError) as exc:
        _confirm(db_session, engine, second, group_id)
    assert exc.value.details["reason"] == "trade_in_valuation_expired"


def test_a_draft_valuation_is_accepted(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id, is_draft=True)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)

    confirmed = _confirm(db_session, engine, contract, group_id)

    assert confirmed.status == ContractStatus.CONFIRMED
    assert derive_status(_reload_valuation(db_session, valuation.id)) == "used"


def test_a_draft_past_its_validity_date_is_still_accepted(db_session, engine):
    """A draft is never 'expired' (derive_status); the refusal is for a
    finalised valuation past its date only."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id, is_draft=True)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)
    valuation.valid_until = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    db_session.commit()
    assert derive_status(valuation) == "draft"

    confirmed = _confirm(db_session, engine, contract, group_id)

    assert confirmed.status == ContractStatus.CONFIRMED
    assert derive_status(_reload_valuation(db_session, valuation.id)) == "used"


def test_a_failed_contract_commit_reverts_used_and_releases_the_reservation(db_session, engine):
    """ADR-047: the valuation call committed on its own, so the failure of
    Sales's own transaction is repaired by a compensating call — never by
    rolling both back together."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, item = _trade_in_contract(db_session, dealership.id, group_id, valuation)

    with (
        patch("app.sales.services.contract.upsert_deal_projection", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError),
    ):
        _confirm(db_session, engine, contract, group_id)

    db_session.rollback()
    db_session.expire_all()
    assert get_stock_item_or_404(db_session, dealership.id, item.id).reservation_state == ReservationState.NONE
    assert db_session.get(type(contract), contract.id).status == ContractStatus.PENDING
    assert derive_status(_reload_valuation(db_session, valuation.id)) == "valid"
    assert db_session.query(OutboxMessage).filter_by(
        aggregate_id=valuation.id, event_type="valuation.valuation.use_reverted"
    ).count() == 1


def test_a_failed_commit_leaves_used_alone_when_another_signed_contract_carries_it(db_session, engine):
    """The case the signed-elsewhere check exists for: THIS confirmation set
    "used", yet another contract is already signed on the valuation — as for
    a contract signed before KAN-101, which never marked it."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    first, _ = _trade_in_contract(db_session, dealership.id, group_id, valuation)
    _confirm(db_session, engine, first, group_id)
    # The pre-KAN-101 state: a signed contract, the valuation still unused.
    stored = _reload_valuation(db_session, valuation.id)
    stored.used_at = None
    db_session.commit()
    second, _ = _trade_in_contract(db_session, dealership.id, group_id, valuation)

    with (
        patch("app.sales.services.contract.upsert_deal_projection", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError),
    ):
        _confirm(db_session, engine, second, group_id)

    db_session.rollback()
    assert derive_status(_reload_valuation(db_session, valuation.id)) == "used"
    assert db_session.query(OutboxMessage).filter_by(
        aggregate_id=valuation.id, event_type="valuation.valuation.use_reverted"
    ).count() == 0


def test_a_refused_reservation_reverts_used(db_session, engine):
    """The valuation is consumed first; if reserve() then refuses (the car
    is already reserved), the confirmation's "used" is undone."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)

    with (
        patch(
            "app.sales.services.contract.reserve",
            side_effect=ConflictError("already reserved", details={"stockItemId": "x"}),
        ),
        pytest.raises(ConflictError),
    ):
        _confirm(db_session, engine, contract, group_id)

    db_session.rollback()
    assert derive_status(_reload_valuation(db_session, valuation.id)) == "valid"
    assert db_session.get(type(contract), contract.id).status == ContractStatus.PENDING


def test_a_failing_release_does_not_stop_the_valuation_revert_or_mask_the_error(db_session, engine):
    """Review finding B: each compensating action runs on its own, and the
    error the caller sees is the one that failed the confirmation."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)

    with (
        patch("app.sales.services.contract.upsert_deal_projection", side_effect=RuntimeError("boom")),
        patch("app.sales.services.contract.release", side_effect=OSError("inventory unreachable")),
        pytest.raises(RuntimeError, match="boom"),
    ):
        _confirm(db_session, engine, contract, group_id)

    db_session.rollback()
    assert derive_status(_reload_valuation(db_session, valuation.id)) == "valid"


def test_cancelling_a_signed_contract_leaves_the_valuation_used(db_session, engine):
    """ADR-066: a used valuation is never edited afterwards."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)
    contract = _confirm(db_session, engine, contract, group_id)

    cancel_contract(
        db_session, contract=contract, reason="Kunde tritt zurück", actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )

    assert derive_status(_reload_valuation(db_session, valuation.id)) == "used"


def test_the_trade_in_pipeline_item_carries_the_valuation_ref(db_session, engine):
    """Exit criterion 1, second half: Stock's valuation_ref_* is written when
    the trade-in becomes a pipeline stock item — the moment it exists."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)
    _confirm(db_session, engine, contract, group_id)
    message = db_session.query(OutboxMessage).filter_by(
        aggregate_id=contract.id, event_type="sales.contract.confirmed"
    ).one()

    handle_sales_contract_confirmed(db_session, tenant_id=dealership.id, payload=message.payload)
    db_session.commit()

    trade_in_item = db_session.query(StockItem).filter_by(
        tenant_id=dealership.id, pipeline_ref=f"contract:{contract.id}:trade_in"
    ).one()
    assert trade_in_item.valuation_ref_id == valuation.id
    assert trade_in_item.valuation_ref_amount == Decimal("12000.00")
    assert trade_in_item.valuation_ref_source == "manual"
    assert trade_in_item.valuation_ref_valued_at == valuation.created_at


def test_a_trade_in_without_a_valuation_leaves_the_ref_empty(db_session):
    tenant_id = uuid.uuid4()
    contract_id = uuid.uuid4()

    handle_sales_contract_confirmed(
        db_session, tenant_id=tenant_id,
        payload={
            "contractId": str(contract_id), "vehicleSource": "existing", "manualConfiguration": None,
            "tradeIn": {"vehicleLabel": "Fiat Panda", "condition": "used"},
        },
    )
    db_session.commit()

    item = db_session.query(StockItem).filter_by(
        tenant_id=tenant_id, pipeline_ref=f"contract:{contract_id}:trade_in"
    ).one()
    assert item.valuation_ref_id is None


def test_a_redelivered_confirmation_leaves_one_trade_in_item_with_its_ref(db_session, engine):
    """At-least-once delivery: the same business event handled twice yields
    one pipeline item (pipeline_ref) whose pointer is still the valuation."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)
    _confirm(db_session, engine, contract, group_id)
    payload = db_session.query(OutboxMessage).filter_by(
        aggregate_id=contract.id, event_type="sales.contract.confirmed"
    ).one().payload

    for _ in range(2):
        handle_sales_contract_confirmed(db_session, tenant_id=dealership.id, payload=payload)
        db_session.commit()

    items = db_session.query(StockItem).filter_by(
        tenant_id=dealership.id, pipeline_ref=f"contract:{contract.id}:trade_in"
    ).all()
    assert len(items) == 1
    assert items[0].valuation_ref_id == valuation.id


@pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"),
    reason="Row locks need Postgres; SQLite (the fast lane) has no SELECT … FOR UPDATE.",
)
def test_two_simultaneous_consumptions_serialise_on_the_row_lock(db_session, engine):
    """Two contracts confirmed at the same moment: the second valuation call
    waits for the first's commit, then sees "used" and publishes nothing."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    holder = factory()
    held = get_valuation_or_404(holder, dealership.id, valuation.id)
    holder.refresh(held, with_for_update=True)  # the first confirmation, mid-call

    result: dict[str, bool] = {}

    def second_confirmation() -> None:
        session = factory()
        try:
            result["newly_used"] = consume_valuation_for_contract(
                session, tenant_id=dealership.id, valuation_id=valuation.id, actor_id=None
            )
        finally:
            session.close()

    thread = threading.Thread(target=second_confirmation)
    thread.start()
    thread.join(1.0)
    blocked = thread.is_alive()
    mark_used(holder, valuation=held, actor_id=None)  # the first one commits, releasing the lock
    holder.close()
    thread.join(10)

    assert blocked
    assert result["newly_used"] is False
    db_session.expire_all()
    assert db_session.query(OutboxMessage).filter_by(
        aggregate_id=valuation.id, event_type="valuation.used"
    ).count() == 1
