"""Valuation's export declaration (KAN-218; FR-A-44; tenant-private,
ADR-029): the Betrieb's valuations with their deductions and contract carries.
The provider quotes the valuations were computed from stay out of the file:
they are the provider's licensed output (residual tables, the provider's own
messages), not the Betrieb's record — the valuation keeps the figures the
Betrieb used. They are deleted with the Betrieb after the retention period.
"""

import uuid
from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.export import ExportedTable, ExportScope, TableExporter, table_of
from app.valuation.models.contract_carry import ValuationContractCarry
from app.valuation.models.valuation import (
    Valuation,
    ValuationDeduction,
    ValuationNumberSequence,
    ValuationProviderQuote,
)


def _tenant(model, **kwargs) -> ExportedTable:
    table = table_of(model)
    return ExportedTable(table=table, scope=lambda scope: table.c.tenant_id == scope.dealership_id, **kwargs)


class ValuationExporter(TableExporter):
    context = "valuation"

    def exported_tables(self) -> Sequence[ExportedTable]:
        return (
            _tenant(Valuation),
            _tenant(ValuationDeduction),
            _tenant(ValuationContractCarry),
            _tenant(ValuationNumberSequence),
            _tenant(
                ValuationProviderQuote,
                in_archive=False,
                exclusion_reason="A provider's licensed valuation output; the valuation keeps the figures used.",
            ),
        )

    def references(self, db: Session, scope: ExportScope) -> Mapping[str, frozenset[uuid.UUID]]:
        ids = db.scalars(
            select(Valuation.customer_id).where(
                Valuation.tenant_id == scope.dealership_id, Valuation.customer_id.is_not(None)
            )
        )
        return {"customer": frozenset(customer_id for customer_id in ids if customer_id is not None)}
