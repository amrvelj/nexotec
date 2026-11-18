"""KAN-461: every case `scripts/measure_list_query_performance.py` times is
a request the Stock grid accepts — every field sorted both ways and every
condition it serves — so the measurement cannot drift from the registry it
measures. (The script itself seeds a throwaway database and is run by hand;
its numbers are in the PR and PRD-Stock.)"""

import importlib.util
from pathlib import Path

from app.core.list_query import parse_list_query
from app.inventory.services.stock_grid import DEFAULT_STOCK_ITEM_SORT, STOCK_ITEM_COLUMNS

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "measure_list_query_performance.py"


def _script():
    spec = importlib.util.spec_from_file_location("measure_list_query_performance", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_measured_case_is_a_request_the_stock_grid_accepts():
    script = _script()
    cases = [case for spec in STOCK_ITEM_COLUMNS for case in script._cases(spec)]

    assert len(cases) == sum(2 + len(spec.allowed_operators) for spec in STOCK_ITEM_COLUMNS)
    for _label, params in cases:
        parse_list_query(
            STOCK_ITEM_COLUMNS,
            filters=[params["filter"]] if "filter" in params else None,
            sort=params.get("sort"),
            default_sort=DEFAULT_STOCK_ITEM_SORT,
            limit=50,
            cursor=None,
        )
