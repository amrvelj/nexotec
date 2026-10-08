"""KAN-115 — nightly reconciliation of the trade-in valuation's «Verwendet»
stamp and of Stock's valuation pointer (CLAUDE.md rule 10). ADR-047 leaves
what a confirmation's failed commit and failed compensation leave undone to
this job; it reports, it never repairs (.claude/rules/events.md).

Rulings exercised here (Anto, 2026-10-07): only a signed deal uses a
valuation, so a stamp with no signed contract behind it is reported whether
a confirmation or a hand set it; a signed contract whose valuation is not
stamped is reported, those signed before KAN-101 included.
"""

import datetime as dt
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage
from app.core.reconciliation import ReconciliationAlarm
from app.core.reconciliation_model import ReconciliationOrphan
from app.inventory import reconciliation as inventory_reconciliation
from app.inventory.models.stock_item import StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.pipeline import handle_sales_contract_confirmed
from app.inventory.services.stock_item import create_stock_item
from app.platform.models.user import User, UserRole, UserStatus
from app.sales import reconciliation as sales_reconciliation
from app.sales.models.contract import ContractStatus, SalesContract
from app.valuation import reconciliation as valuation_reconciliation
from app.valuation.models.valuation import Valuation
from tests.test_sales_lifecycle_reservation import _dealership
from tests.test_sales_trade_in_valuation_use import _confirm, _trade_in_contract, _valuation

USED_WITHOUT_SIGNED_CONTRACT = "used valuation with no signed contract carrying it"
SIGNED_WITHOUT_USED_VALUATION = "signed contract whose trade-in valuation is not used"
POINTER_TO_NO_VALUATION = "stock_item.valuation_ref_id -> valuation.id"
POINTER_AMOUNT_DIFFERS = "stock_item.valuation_ref_amount differs from valuation.final_offer"


def _findings(job, db_session, label: str) -> list[ReconciliationOrphan]:
    """Runs one context's whole job and keeps the findings of one check —
    the other checks of the job are not what these tests are about."""

    try:
        job.run(db_session)
    except ReconciliationAlarm as alarm:
        return [orphan for orphan in alarm.orphans if orphan.check_label == label]
    return []


def _stamped(db_session, valuation: Valuation, *, hours_ago: float) -> Valuation:
    valuation.used_at = utcnow() - dt.timedelta(hours=hours_ago)
    db_session.commit()
    return valuation


def _contract(db_session, tenant_id, valuation_id, *, signed: bool, status=ContractStatus.CONFIRMED) -> SalesContract:
    contract = SalesContract(
        tenant_id=tenant_id, contract_number=f"C-{uuid.uuid4().hex[:6]}", status=status,
        trade_in_valuation_id=valuation_id, signed_at=utcnow() - dt.timedelta(days=30) if signed else None,
    )
    db_session.add(contract)
    db_session.commit()
    return contract


# --- valuation: «Verwendet» with no signed contract behind it -------------------


def test_a_used_valuation_with_no_signed_contract_is_reported(db_session):
    """A stamp no signed deal backs — set by hand before KAN-115, or left by
    a confirmation that never completed. Recorded against the valuation."""

    dealership = _dealership(db_session)
    valuation = _stamped(db_session, _valuation(db_session, dealership.id, uuid.uuid4()), hours_ago=2)

    findings = _findings(valuation_reconciliation, db_session, USED_WITHOUT_SIGNED_CONTRACT)

    assert [(f.source_table, f.source_row_id, f.dangling_value) for f in findings] == [
        ("valuation", valuation.id, valuation.id)
    ]


def test_a_used_valuation_whose_only_contracts_are_unsigned_or_another_dealerships_is_reported(db_session):
    dealership = _dealership(db_session)
    valuation = _stamped(db_session, _valuation(db_session, dealership.id, uuid.uuid4()), hours_ago=2)
    _contract(db_session, dealership.id, valuation.id, signed=False, status=ContractStatus.PENDING)
    _contract(db_session, uuid.uuid4(), valuation.id, signed=True)

    assert len(_findings(valuation_reconciliation, db_session, USED_WITHOUT_SIGNED_CONTRACT)) == 1


@pytest.mark.parametrize("status", [ContractStatus.CONFIRMED, ContractStatus.CANCELLED, ContractStatus.INVOICED])
def test_a_used_valuation_a_signed_contract_carries_is_not_reported(db_session, status):
    """Signed is `signed_at` set, whatever the status since: cancelling a
    signed contract leaves its valuation used (ADR-066)."""

    dealership = _dealership(db_session)
    valuation = _stamped(db_session, _valuation(db_session, dealership.id, uuid.uuid4()), hours_ago=2)
    _contract(db_session, dealership.id, valuation.id, signed=True, status=status)

    assert _findings(valuation_reconciliation, db_session, USED_WITHOUT_SIGNED_CONTRACT) == []


def test_a_stamp_from_a_confirmation_still_in_progress_is_not_reported(db_session):
    """A confirmation stamps the valuation on its own commit before the
    contract is signed in the next (ADR-047): within the grace period the
    missing signature is not yet a finding."""

    dealership = _dealership(db_session)
    _stamped(db_session, _valuation(db_session, dealership.id, uuid.uuid4()), hours_ago=0)

    assert _findings(valuation_reconciliation, db_session, USED_WITHOUT_SIGNED_CONTRACT) == []


def test_an_unused_valuation_is_not_reported(db_session):
    dealership = _dealership(db_session)
    _valuation(db_session, dealership.id, uuid.uuid4())

    assert _findings(valuation_reconciliation, db_session, USED_WITHOUT_SIGNED_CONTRACT) == []


def test_a_confirmation_whose_commit_and_compensation_both_failed_is_reported(db_session, engine):
    """The case the ticket exists for: the valuation call committed, the
    contract's own commit failed, and the compensating revert failed too —
    ADR-047 leaves it to nightly reconciliation, which now sees it."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation)

    with (
        patch("app.sales.services.contract.upsert_deal_projection", side_effect=RuntimeError("boom")),
        patch("app.sales.services.contract.revert_valuation_use", side_effect=OSError("valuation unreachable")),
        pytest.raises(RuntimeError, match="boom"),
    ):
        _confirm(db_session, engine, contract, group_id)
    db_session.rollback()
    db_session.expire_all()
    assert db_session.get(SalesContract, contract.id).status == ContractStatus.PENDING
    _stamped(db_session, db_session.get(Valuation, valuation.id), hours_ago=2)  # the grace period has passed

    findings = _findings(valuation_reconciliation, db_session, USED_WITHOUT_SIGNED_CONTRACT)

    assert [f.source_row_id for f in findings] == [valuation.id]


# --- sales: a signed contract whose valuation is not «Verwendet» ----------------


@pytest.mark.parametrize("status", [ContractStatus.CONFIRMED, ContractStatus.CANCELLED])
def test_a_signed_contract_whose_valuation_is_not_used_is_reported(db_session, status):
    """A contract signed before KAN-101 (which stamped nothing), or one that
    lost the stamp to another confirmation's compensation in the race the
    ticket names. Recorded against the contract."""

    dealership = _dealership(db_session)
    valuation = _valuation(db_session, dealership.id, uuid.uuid4())
    contract = _contract(db_session, dealership.id, valuation.id, signed=True, status=status)

    findings = _findings(sales_reconciliation, db_session, SIGNED_WITHOUT_USED_VALUATION)

    assert [(f.source_table, f.source_row_id, f.dangling_value) for f in findings] == [
        ("sales_contract", contract.id, contract.id)
    ]


def test_signed_contracts_with_a_used_valuation_and_unsigned_ones_are_not_reported(db_session):
    dealership = _dealership(db_session)
    used = _stamped(db_session, _valuation(db_session, dealership.id, uuid.uuid4()), hours_ago=2)
    _contract(db_session, dealership.id, used.id, signed=True)
    unused = _valuation(db_session, dealership.id, uuid.uuid4())
    _contract(db_session, dealership.id, unused.id, signed=False, status=ContractStatus.PENDING)
    _contract(db_session, dealership.id, None, signed=True)  # no trade-in valuation at all

    assert _findings(sales_reconciliation, db_session, SIGNED_WITHOUT_USED_VALUATION) == []


def test_a_signed_contract_naming_no_existing_valuation_is_left_to_the_reference_check(db_session):
    """A trade_in_valuation_id that resolves to nothing is a dangling
    reference — KAN-145's check of sales_contract's references, not this one."""

    dealership = _dealership(db_session)
    _contract(db_session, dealership.id, uuid.uuid4(), signed=True)

    assert _findings(sales_reconciliation, db_session, SIGNED_WITHOUT_USED_VALUATION) == []


def test_a_signed_contract_naming_another_dealerships_valuation_is_left_to_the_reference_check(db_session):
    """A valuation is tenant-private (ADR-029) and the hand stamp looks for
    signed contracts in the valuation's own dealership, so this check asks
    only about a valuation of the contract's own dealership: a finding here
    could never be cleared. Another dealership's id on a contract is a
    reference problem — KAN-145's check, not this one."""

    dealership, other = _dealership(db_session), _dealership(db_session)
    foreign = _valuation(db_session, other.id, uuid.uuid4())
    _contract(db_session, dealership.id, foreign.id, signed=True)

    assert _findings(sales_reconciliation, db_session, SIGNED_WITHOUT_USED_VALUATION) == []


def test_a_signed_contract_whose_valuation_was_stamped_by_hand_since_is_no_longer_reported(db_session):
    """The repair path the finding points to: «Als verwendet markieren»
    stamps the valuation a signed contract carries, expired or not."""

    from app.valuation.services.valuation import mark_used_by_hand

    dealership = _dealership(db_session)
    valuation = _valuation(db_session, dealership.id, uuid.uuid4())
    valuation.valid_until = utcnow() - dt.timedelta(days=1)
    db_session.commit()
    _contract(db_session, dealership.id, valuation.id, signed=True)
    assert len(_findings(sales_reconciliation, db_session, SIGNED_WITHOUT_USED_VALUATION)) == 1

    mark_used_by_hand(db_session, valuation=valuation, actor_id=None)

    assert _findings(sales_reconciliation, db_session, SIGNED_WITHOUT_USED_VALUATION) == []


# --- inventory: Stock's valuation pointer ---------------------------------------


def _trade_in_item(db_session, tenant_id, *, valuation_id, amount) -> StockItem:
    item = create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="VW Golf 1.5 TSI", condition=StockItemCondition.USED, vin="WVWZZZ1KZAW000115"),
        actor_id=None,
    )
    item.valuation_ref_id = valuation_id
    item.valuation_ref_amount = amount
    item.valuation_ref_valued_at = utcnow()
    item.valuation_ref_source = "manual"
    db_session.commit()
    return item


def test_a_pointer_to_a_valuation_that_does_not_exist_is_reported(db_session):
    dealership = _dealership(db_session)
    missing = uuid.uuid4()
    item = _trade_in_item(db_session, dealership.id, valuation_id=missing, amount=Decimal("12000.00"))

    findings = _findings(inventory_reconciliation, db_session, POINTER_TO_NO_VALUATION)

    assert [(f.source_row_id, f.target_table, f.dangling_value) for f in findings] == [(item.id, "valuation", missing)]
    assert _findings(inventory_reconciliation, db_session, POINTER_AMOUNT_DIFFERS) == []


@pytest.mark.parametrize("amount", [Decimal("11500.00"), None])
def test_a_pointer_whose_amount_differs_from_the_valuation_is_reported(db_session, amount):
    """Stock copies final_offer when it creates the trade-in item (KAN-101); a
    valuation is never edited after creation (ADR-066), so any difference —
    an amount missing included — is a copy gone wrong."""

    dealership = _dealership(db_session)
    valuation = _valuation(db_session, dealership.id, uuid.uuid4())  # final_offer 12000.00
    item = _trade_in_item(db_session, dealership.id, valuation_id=valuation.id, amount=amount)

    findings = _findings(inventory_reconciliation, db_session, POINTER_AMOUNT_DIFFERS)

    assert [(f.source_table, f.source_row_id, f.dangling_value) for f in findings] == [
        ("stock_item", item.id, item.id)
    ]


def test_a_matching_pointer_and_no_pointer_are_not_reported(db_session):
    dealership = _dealership(db_session)
    valuation = _valuation(db_session, dealership.id, uuid.uuid4())
    _trade_in_item(db_session, dealership.id, valuation_id=valuation.id, amount=Decimal("12000.00"))
    create_stock_item(
        db_session, tenant_id=dealership.id,
        data=StockItemCreate(vehicle_label="Skoda Fabia", condition=StockItemCondition.USED, vin="TMBEA6NJ0LZ000115"),
        actor_id=None,
    )

    assert _findings(inventory_reconciliation, db_session, POINTER_TO_NO_VALUATION) == []
    assert _findings(inventory_reconciliation, db_session, POINTER_AMOUNT_DIFFERS) == []


# --- end to end: a confirmation that completes leaves all three jobs clean ------


def test_a_completed_confirmation_with_a_trade_in_leaves_all_three_jobs_clean(db_session, engine):
    """No false positive from the real flow: confirm the contract (valuation
    stamped, contract signed), let Stock create the trade-in item with its
    pointer, then run the three jobs whole, past the grace period. A real
    user writes the deal, as the signed-in seller always does (KAN-145
    checks the actor columns)."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    seller = User(
        tenant_id=dealership.id, first_name="Sam", last_name="Sales", email=f"sam-{uuid.uuid4().hex[:8]}@example.ch",
        role=UserRole.SALES, access_roles=["sales"], status=UserStatus.ACTIVE, auth_identity_id=str(uuid.uuid4()),
    )
    db_session.add(seller)
    db_session.commit()
    valuation = _valuation(db_session, dealership.id, group_id)
    contract, _item = _trade_in_contract(db_session, dealership.id, group_id, valuation, actor_id=seller.id)
    _confirm(db_session, engine, contract, group_id, actor_id=seller.id)
    message = db_session.query(OutboxMessage).filter_by(
        aggregate_id=contract.id, event_type="sales.contract.confirmed"
    ).one()
    handle_sales_contract_confirmed(db_session, tenant_id=dealership.id, payload=message.payload)
    db_session.commit()
    _stamped(db_session, db_session.get(Valuation, valuation.id), hours_ago=2)

    for job in (valuation_reconciliation, sales_reconciliation, inventory_reconciliation):
        run = job.run(db_session)
        assert (run.context, run.orphans_found) == (job.CONTEXT, 0)
