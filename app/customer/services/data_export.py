"""Customers' export declaration (KAN-218; FR-A-44, ADR-014).

Customers are owned by the dealer group, so a Betrieb's export holds its own
relationship only (FR-A-44): the customers it created (`dealership_id`, the
provenance column), the customers its history names — the ids the other
contexts' exported rows carry (`ExportScope.references["customer"]`: offers,
contracts, deals, valuations) — and, in
a group of one, every customer of the group (one that predates provenance
too). Their contact channels, tags, contact groups, external ids, vehicle
links and legal bases come with them.

At the deletion after the retention period, the Betrieb's own bookings go.
The customers stay with the group while a sister Betrieb still holds data
(`ExportScope.sole_in_group` false); once none does, the group's customers
and everything hanging off them are deleted.

The group predicate below is the Betrieb's OWN group, handed in by platform —
never another's: `tests/architecture/test_no_ambient_group_read.py` lists
this file for that reason.
"""

from collections.abc import Mapping, Sequence

from sqlalchemy import or_, select

from app.core.export import ExportedTable, ExportScope, TableExporter, table_of
from app.customer.models.customer import (
    Customer,
    CustomerAddress,
    CustomerContactGroup,
    CustomerEmail,
    CustomerExternalId,
    CustomerNumberSequence,
    CustomerPhone,
    CustomerTag,
)
from app.customer.models.legal_basis import LegalBasis
from app.customer.models.stock_item_booking import StockItemBooking
from app.customer.models.vehicle_party import VehicleParty

_customer = table_of(Customer)
_booking = table_of(StockItemBooking)


def _related_customer_ids(scope: ExportScope):
    """The ids of the customers in the Betrieb's relationship."""

    own_group = _customer.c.group_id == scope.group_id
    if scope.group_is_single:
        return select(_customer.c.id).where(own_group)
    return select(_customer.c.id).where(
        own_group,
        or_(
            _customer.c.dealership_id == scope.dealership_id,
            _customer.c.id.in_(sorted(scope.referenced("customer"))),
        ),
    )


def _group_customer_ids(scope: ExportScope):
    return select(_customer.c.id).where(_customer.c.group_id == scope.group_id)


def _child(model) -> ExportedTable:
    table = table_of(model)
    return ExportedTable(
        table=table,
        scope=lambda scope: table.c.customer_id.in_(_related_customer_ids(scope)),
        group_owned=True,
        erase_scope=lambda scope: table.c.customer_id.in_(_group_customer_ids(scope)),
        depends_on=(_customer,),
    )


class CustomerExporter(TableExporter):
    context = "customer"

    def exported_tables(self) -> Sequence[ExportedTable]:
        sequence = table_of(CustomerNumberSequence)
        return (
            ExportedTable(
                table=_customer,
                scope=lambda scope: _customer.c.id.in_(_related_customer_ids(scope)),
                group_owned=True,
                erase_scope=lambda scope: _customer.c.group_id == scope.group_id,
            ),
            _child(CustomerAddress),
            _child(CustomerEmail),
            _child(CustomerPhone),
            _child(CustomerExternalId),
            _child(CustomerTag),
            _child(CustomerContactGroup),
            _child(VehicleParty),
            _child(LegalBasis),
            ExportedTable(table=_booking, scope=lambda scope: _booking.c.tenant_id == scope.dealership_id),
            ExportedTable(
                table=sequence,
                scope=lambda scope: sequence.c.group_id == scope.group_id,
                in_archive=False,
                exclusion_reason="The group's customer-number counter; the numbers are on the customers.",
                group_owned=True,
                marks_write=False,
            ),
        )

    def excluded_tables(self) -> Mapping[str, str]:
        return {}
