"""Aftersales' export declaration (KAN-218; FR-A-44): the Betrieb's workshop
orders with their jobs, the customer's answers (KAN-811) and positions (KAN-810), the stays of
the cars with the workshop status (KAN-813), and the service catalogue's
packages with their positions (KAN-818). All three are the Betrieb's
own (`tenant_id`); the contacts they name are exported for the Betrieb's
relationship through `references`."""

import uuid
from collections.abc import Mapping, Sequence

from sqlalchemy import ScalarResult, select, union
from sqlalchemy.orm import Session

from app.aftersales.models.service_order import (
    ServiceOrder,
    ServiceOrderJob,
    ServiceOrderJobAnswer,
    ServiceOrderPosition,
)
from app.aftersales.models.service_package import ServicePackage, ServicePackagePosition
from app.aftersales.models.service_stay import ServiceOrderStay, ServiceOrderWorkshopStatus, ServiceStay
from app.core.export import ExportedTable, ExportScope, TableExporter, table_of


def _tenant(model) -> ExportedTable:
    table = table_of(model)
    return ExportedTable(table=table, scope=lambda scope: table.c.tenant_id == scope.dealership_id)


class AftersalesExporter(TableExporter):
    context = "aftersales"

    def exported_tables(self) -> Sequence[ExportedTable]:
        return (
            _tenant(ServiceOrder),
            _tenant(ServiceOrderJob),
            _tenant(ServiceOrderJobAnswer),
            _tenant(ServiceOrderPosition),
            _tenant(ServicePackage),
            _tenant(ServicePackagePosition),
            _tenant(ServiceStay),
            _tenant(ServiceOrderStay),
            _tenant(ServiceOrderWorkshopStatus),
        )

    def references(self, db: Session, scope: ExportScope) -> Mapping[str, frozenset[uuid.UUID]]:
        mine = ServiceOrder.tenant_id == scope.dealership_id
        ids: ScalarResult[uuid.UUID | None] = db.scalars(
            union(
                select(ServiceOrder.contact_id).where(mine),
                select(ServiceOrder.invoice_recipient_contact_id).where(mine),
                select(ServiceOrder.driver_contact_id).where(mine, ServiceOrder.driver_contact_id.is_not(None)),
            )
        )
        return {"contact": frozenset(contact_id for contact_id in ids if contact_id is not None)}
