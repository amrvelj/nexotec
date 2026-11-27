"""Aftersales' outbound cross-context references (KAN-810; rule 10, ADR-107).

A workshop order names, by plain GUIDs (the three-column pattern, rule 2):
its Betrieb and location (platform), its contact, invoice recipient and
driver (contact), and its car (vehicle); a labour position names its labour
category (platform); every row names the users who wrote it. Each must name
a row that exists — an orphan is reported, never repaired. Read-only; see
app.core.reconciliation for the mechanism.

Beyond existence (slice 1, section 6; KAN-810 review): an order that is not
cancelled must not name a merged contact (contact, invoice recipient or
driver), a contact outside its Betrieb's dealer group (ADR-014), or a merged
vehicle record. Since KAN-837 the consumers `aftersales.contact_merged` and
`aftersales.vehicle_merged` re-point the orders (`services/order_references.py`),
so these StateChecks find an order only when a consumer has not run or failed
(its dead letter alerts too). The service catalogue (KAN-818): a package position names its labour
category (platform) and every package row its Betrieb and writers.

A cancelled order is history and is left as it is;
a ready-for-billing order is reported, since its invoice still names them.
A merge is judged after a day's grace, as the labour-rate agreements' check
does (KAN-806): a consumer's re-point in flight is not a finding.

Not checked here: a position's `labour_rate_id`, which names a platform rate
or agreement row by `rate_source` and is provenance, never read back (a rate
row is never deleted, ruling 178).
"""

import datetime as dt
from typing import Any

from sqlalchemy import ColumnElement
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.aftersales.models.service_order import (
    ServiceOrder,
    ServiceOrderJob,
    ServiceOrderJobAnswer,
    ServiceOrderPosition,
    ServiceOrderState,
)
from app.aftersales.models.service_package import ServicePackage, ServicePackagePosition
from app.aftersales.models.service_stay import ServiceOrderStay, ServiceStay
from app.contact.public import contact_exists, contact_in_group, contact_is_merged, contact_reference_check
from app.core.base import utcnow
from app.core.reconciliation import ReconciliationRun, ReferenceCheck, StateCheck, run_reconciliation
from app.platform.public import (
    actor_reference_checks,
    dealership_group_of,
    dealership_reference_check,
    labour_rate_category_reference_check,
    location_reference_check,
)
from app.vehicle.public import vehicle_is_merged, vehicle_reference_check

CONTEXT = "aftersales"

ACTOR_TABLES = (ServiceOrder, ServiceOrderJob, ServiceOrderJobAnswer, ServiceOrderPosition, ServiceStay,
                ServiceOrderStay, ServicePackage, ServicePackagePosition)

_MERGE_GRACE = dt.timedelta(days=1)

# The order's contacts: the customer, the invoice recipient and the driver.
_CONTACT_COLUMNS: tuple[InstrumentedAttribute[Any], ...] = (
    ServiceOrder.contact_id,
    ServiceOrder.invoice_recipient_contact_id,
    ServiceOrder.driver_contact_id,
)


def _live() -> ColumnElement[bool]:
    return ServiceOrder.state != ServiceOrderState.CANCELLED


def _names_a_merged_contact(column: InstrumentedAttribute[Any]) -> StateCheck:
    return StateCheck(
        label=f"service_order.{column.key} names a merged contact",
        source_model=ServiceOrder,
        source_row_id_column=ServiceOrder.id,
        where=lambda: _live()
        & contact_is_merged(column, merged_before=utcnow() - _MERGE_GRACE).correlate(ServiceOrder),
    )


def _names_a_contact_outside_the_group(column: InstrumentedAttribute[Any]) -> StateCheck:
    def where() -> ColumnElement[bool]:
        group = dealership_group_of(ServiceOrder.tenant_id, correlate=ServiceOrder)
        named = contact_exists(column).correlate(ServiceOrder)
        return _live() & named & ~contact_in_group(column, group).correlate(ServiceOrder)

    return StateCheck(
        label=f"service_order.{column.key} names a contact outside the business's group",
        source_model=ServiceOrder,
        source_row_id_column=ServiceOrder.id,
        where=where,
    )

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
    # KAN-813: the stay's car and location (ADR-213).
    vehicle_reference_check(
        label="service_stay.vehicle_id -> vehicle_mdm.id",
        source_model=ServiceStay,
        source_row_id_column=ServiceStay.id,
        source_fk_column=ServiceStay.vehicle_id,
    ),
    location_reference_check(
        label="service_stay.location_id -> location.id",
        source_model=ServiceStay,
        source_row_id_column=ServiceStay.id,
        source_fk_column=ServiceStay.location_id,
    ),
    labour_rate_category_reference_check(
        label="service_order_position.labour_category_id -> labour_rate_category.id",
        source_model=ServiceOrderPosition,
        source_row_id_column=ServiceOrderPosition.id,
        source_fk_column=ServiceOrderPosition.labour_category_id,
        nullable=True,  # a text position names none
    ),
    labour_rate_category_reference_check(
        label="service_package_position.labour_category_id -> labour_rate_category.id",
        source_model=ServicePackagePosition,
        source_row_id_column=ServicePackagePosition.id,
        source_fk_column=ServicePackagePosition.labour_category_id,
        nullable=True,  # a text position names none
    ),
    *(_names_a_merged_contact(column) for column in _CONTACT_COLUMNS),
    *(_names_a_contact_outside_the_group(column) for column in _CONTACT_COLUMNS),
    StateCheck(
        label="service_order.vehicle_id names a merged vehicle record",
        source_model=ServiceOrder,
        source_row_id_column=ServiceOrder.id,
        where=lambda: _live()
        & vehicle_is_merged(ServiceOrder.vehicle_id, updated_before=utcnow() - _MERGE_GRACE, correlate=ServiceOrder),
    ),
    *actor_reference_checks(*ACTOR_TABLES),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)
