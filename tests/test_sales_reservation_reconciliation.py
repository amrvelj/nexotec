"""WP-8 PR-6: the orphan-reservation sweep app.inventory.services.
reservation's own docstring flags as "PR-7/WP-8 follow-up work"."""

import uuid

from sqlalchemy.orm import sessionmaker

from app.inventory.models.stock_item import ReservationState, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.stock_item import create_stock_item, get_stock_item_or_404
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import confirm_contract, create_contract
from app.sales.services.offer import create_offer, update_offer
from app.sales.services.reservation_reconciliation import release_orphaned_reservations


def _session_factory(engine):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return lambda: factory()


def test_release_orphaned_reservations_leaves_a_confirmed_contract_alone(db_session, engine):
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Cupra Formentor", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
        actor_id=uuid.uuid4(),
    )
    contract = create_contract(db_session, tenant_id=tenant_id, offer=offer, actor_id=uuid.uuid4())
    confirm_contract(
        db_session, contract=contract, group_id=uuid.uuid4(), actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )

    db_session.expire_all()
    released = release_orphaned_reservations(db_session, tenant_id=tenant_id)
    assert released == []

    db_session.expire_all()
    refreshed = get_stock_item_or_404(db_session, tenant_id, item.id)
    assert refreshed.reservation_state == ReservationState.RESERVED


def test_release_orphaned_reservations_releases_a_cancelled_contracts_reservation(db_session, engine):
    """A reservation whose owning contract is no longer CONFIRMED (e.g. it
    was cancelled through some path that didn't itself release it) is an
    orphan the sweep must clean up.
    """

    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Nissan Qashqai", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
        actor_id=uuid.uuid4(),
    )
    contract = create_contract(db_session, tenant_id=tenant_id, offer=offer, actor_id=uuid.uuid4())
    contract = confirm_contract(
        db_session, contract=contract, group_id=uuid.uuid4(), actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )

    # Simulate an orphan: the reservation stays RESERVED on the stock item
    # (as if cancel_contract's own release() call had been skipped) while
    # the contract itself is no longer CONFIRMED.
    from app.sales.models.contract import ContractStatus

    contract.status = ContractStatus.CANCELLED
    db_session.commit()

    db_session.expire_all()
    released = release_orphaned_reservations(db_session, tenant_id=tenant_id)
    assert released == [item.id]

    db_session.expire_all()
    refreshed = get_stock_item_or_404(db_session, tenant_id, item.id)
    assert refreshed.reservation_state == ReservationState.NONE
