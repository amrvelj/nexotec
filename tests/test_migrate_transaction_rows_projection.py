"""KAN-678 — a legacy transaction import leaves no standing reconciliation
finding behind it.

`scripts/migrate_transaction_rows.py` writes contracts without publishing
`sales.contract.*`, so Stock's projection (`inventory_sales_contract`,
KAN-630) never hears of them; the nightly check «sales_contract missing from
inventory_sales_contract» named each one every night until someone re-ran
the projection backfill. A --commit run that wrote a contract now ends by
running that backfill itself — on an environment where it has been recorded
before; where it has not, the first run is still due and covers them.
"""

import pytest
from sqlalchemy import select

from app.inventory.models.sales_contract import InventorySalesContract, InventorySalesContractBackfill
from app.inventory.models.stock_item import StockItem
from app.inventory.services.sales_contract import record_backfill
from app.sales.models.contract import SalesContract
from scripts import migrate_transaction_rows
from scripts.migrate_transaction_rows import main
from tests.test_inventory_reservation_sweep import _session_factory
from tests.test_inventory_sales_contract_projection import _MISSING, _age, _findings
from tests.test_migrate_transaction_rows import (
    _customer,
    _dealership,
    _legacy_vehicle,
    _migrated_vehicle_mdm,
    _sale_transaction,
    _trade_in_transaction,
)

_REFRESHED = "KAN-630 Stock's projection of Sales' contracts — backfill (COMMITTED)"
_NOT_REFRESHED = "Stock's projection of Sales' contracts: NOT refreshed"


@pytest.fixture(autouse=True)
def _the_script_runs_on_the_test_database(engine, monkeypatch):
    monkeypatch.setattr(migrate_transaction_rows, "SessionLocal", _session_factory(engine))


def _a_legacy_sale(db_session):
    _dealership(db_session)
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    txn = _sale_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()
    return txn


def _a_backfill_has_run(db_session) -> None:
    record_backfill(db_session, contracts_read=0, rows_written=0)
    db_session.commit()


def _imported_contract(db_session, txn) -> SalesContract:
    db_session.expire_all()
    return db_session.scalars(select(SalesContract).where(SalesContract.legacy_transaction_id == txn.id)).one()


def _projection_rows(db_session) -> list[InventorySalesContract]:
    db_session.expire_all()
    return list(db_session.scalars(select(InventorySalesContract)).all())


def _backfill_runs(db_session) -> int:
    db_session.expire_all()
    return len(db_session.scalars(select(InventorySalesContractBackfill)).all())


def test_an_import_after_the_backfill_leaves_no_contract_missing_from_the_projection(db_session, capsys):
    txn = _a_legacy_sale(db_session)
    _a_backfill_has_run(db_session)

    assert main(["--commit"]) == 0

    contract = _imported_contract(db_session, txn)
    assert [row.contract_id for row in _projection_rows(db_session)] == [contract.id]
    assert _backfill_runs(db_session) == 2  # the earlier run and the import's own
    assert _REFRESHED in capsys.readouterr().out
    _age(db_session, contract.id)  # past the check's one-hour grace
    assert _MISSING not in _findings(db_session)


def test_before_the_first_backfill_the_import_records_no_run_and_says_so(db_session, capsys):
    """Recording a run tells the orphan sweep to trust the projection; that
    is the first backfill's to say, not the import's."""

    txn = _a_legacy_sale(db_session)

    assert main(["--commit"]) == 0

    contract = _imported_contract(db_session, txn)
    assert _projection_rows(db_session) == []
    assert _backfill_runs(db_session) == 0
    out = capsys.readouterr().out
    assert _NOT_REFRESHED in out and _REFRESHED not in out
    _age(db_session, contract.id)
    assert _findings(db_session).get(_MISSING) == {contract.id}  # until the first backfill runs


def test_a_dry_run_never_touches_the_projection(db_session, capsys):
    _a_legacy_sale(db_session)
    _a_backfill_has_run(db_session)

    assert main([]) == 0

    assert _projection_rows(db_session) == []
    assert _backfill_runs(db_session) == 1
    out = capsys.readouterr().out
    assert _REFRESHED not in out and _NOT_REFRESHED not in out


def test_an_import_that_wrote_no_contract_does_not_run_the_backfill(db_session, capsys):
    _dealership(db_session)
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    _trade_in_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()
    _a_backfill_has_run(db_session)

    assert main(["--commit"]) == 0

    db_session.expire_all()
    assert db_session.query(StockItem).count() == 1  # the trade-in did migrate
    assert _backfill_runs(db_session) == 1
    out = capsys.readouterr().out
    assert _REFRESHED not in out and _NOT_REFRESHED not in out


def test_a_failed_refresh_keeps_the_import_and_exits_non_zero(db_session, monkeypatch, capsys):
    txn = _a_legacy_sale(db_session)
    _a_backfill_has_run(db_session)

    def boom() -> str:
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(migrate_transaction_rows, "_refresh_stock_projection", boom)

    assert main(["--commit"]) == 1

    assert _imported_contract(db_session, txn) is not None  # committed before the refresh
    out = capsys.readouterr().out
    assert "REFRESH FAILED" in out and "RuntimeError: simulated failure" in out
    assert "backfill_inventory_sales_contract_projection.py --commit" in out
