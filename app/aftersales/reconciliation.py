"""Aftersales' outbound cross-context references (KAN-810; rule 10, ADR-107).

A workshop order names, by plain GUIDs (the three-column pattern, rule 2):
its Betrieb and location (platform), its contact, invoice recipient and
driver (contact), and its car (vehicle); a labour position names its labour
category (platform); every row names the users who wrote it. Each must name
a row that exists — an orphan is reported, never repaired. Read-only; see
app.core.reconciliation for the mechanism.

Not checked here: a position's `labour_rate_id`, which names a platform rate
or agreement row by `rate_source` and is provenance, never read back (a rate
row is never deleted, ruling 178).
"""

from sqlalchemy.orm import Session

from app.aftersales.models.service_order import ServiceOrder, ServiceOrderJob, ServiceOrderPosition
from app.contact.public import contact_reference_check
from app.core.reconciliation import ReconciliationRun, ReferenceCheck, StateCheck, run_reconciliation
from app.platform.public import (
    actor_reference_checks,
    dealership_reference_check,
    labour_rate_category_reference_check,
    location_reference_check,
)
from app.vehicle.public import vehicle_reference_check

CONTEXT = "aftersales"

ACTOR_TABLES = (ServiceOrder, ServiceOrderJob, ServiceOrderPosition)

CHECKS: list[ReferenceCheck | StateCheck] = [
    *(
        dealership_reference_check(
            label=f"{model.__tablename__}.tenant_id -> dealership.id",
            source_model=model,
            source_row_id_column=model.id,
            source_fk_column=model.tenant_id,
        )
        for model in ACTOR_TABLES
    ),
    location_reference_check(
        label="service_order.location_id -> location.id",
        source_model=ServiceOrder,
        source_row_id_column=ServiceOrder.id,
        source_fk_column=ServiceOrder.location_id,
    ),
    contact_reference_check(
        label="service_order.contact_id -> contact.id",
        source_model=ServiceOrder,
        source_row_id_column=ServiceOrder.id,
        source_fk_column=ServiceOrder.contact_id,
    ),
    contact_reference_check(
        label="service_order.invoice_recipient_contact_id -> contact.id",
        source_model=ServiceOrder,
        source_row_id_column=ServiceOrder.id,
        source_fk_column=ServiceOrder.invoice_recipient_contact_id,
    ),
    contact_reference_check(
        label="service_order.driver_contact_id -> contact.id",
        source_model=ServiceOrder,
        source_row_id_column=ServiceOrder.id,
        source_fk_column=ServiceOrder.driver_contact_id,
        nullable=True,
    ),
    vehicle_reference_check(
        label="service_order.vehicle_id -> vehicle_mdm.id",
        source_model=ServiceOrder,
        source_row_id_column=ServiceOrder.id,
        source_fk_column=ServiceOrder.vehicle_id,
    ),
    labour_rate_category_reference_check(
        label="service_order_position.labour_category_id -> labour_rate_category.id",
        source_model=ServiceOrderPosition,
        source_row_id_column=ServiceOrderPosition.id,
        source_fk_column=ServiceOrderPosition.labour_category_id,
        nullable=True,  # a text position names none
    ),
    *actor_reference_checks(*ACTOR_TABLES),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)
