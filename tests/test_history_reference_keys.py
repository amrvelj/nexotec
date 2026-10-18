"""KAN-371: the History tab marks an unnamed id as unknown only under the keys
that reference a record (`REFERENCE_KEYS` in HistoryTab.tsx), and the History
endpoint names exactly the keys `history_labels` knows. The two lists live on
either side of the API; a key added to one and not the other would render a
bare UUID, or hide a real value behind the marker."""

import re
from pathlib import Path

from app.customer.services import customer as customer_service

_HISTORY_TAB = Path(__file__).resolve().parents[1] / "frontend/apps/dms/src/components/customer-detail/HistoryTab.tsx"


def test_the_tab_marks_exactly_the_keys_the_endpoint_names():
    source = _HISTORY_TAB.read_text(encoding="utf-8")
    block = re.search(r"const REFERENCE_KEYS = new Set\(\[(.*?)\]\)", source, re.DOTALL)
    assert block is not None, "REFERENCE_KEYS not found in HistoryTab.tsx"
    frontend = set(re.findall(r"'([A-Za-z]+)'", block.group(1)))

    backend = (
        customer_service._HISTORY_DEALERSHIP_KEYS
        | customer_service._HISTORY_USER_KEYS
        | customer_service._HISTORY_CUSTOMER_KEYS
        | customer_service._HISTORY_VEHICLE_KEYS
        | customer_service._HISTORY_PARTY_KEYS
    )

    assert frontend == backend
