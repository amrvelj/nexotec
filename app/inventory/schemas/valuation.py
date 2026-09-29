"""WP-7 PR-9 (ADR-066/ADR-048) — a read-only denormalized pointer. There
is deliberately no create/update schema here — Stock is a reader, never
the module that models a valuation's own inputs/deductibles/status
(app.valuation is).
"""

import datetime as dt
import uuid
from decimal import Decimal

from app.core.schemas import CamelModel


class ValuationRefRead(CamelModel):
    valuation_id: uuid.UUID | None
    amount: Decimal | None
    valued_at: dt.datetime | None
    source: str | None
