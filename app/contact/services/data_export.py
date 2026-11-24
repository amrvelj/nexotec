"""Contacts' export declaration (KAN-218; FR-A-44, ADR-014).

Contacts are owned by the dealer group, so a Betrieb's export holds its own
relationship only (FR-A-44): the contacts it created (`dealership_id`, the
provenance column), the contacts its history names — the ids the other
contexts' exported rows carry (`ExportScope.references["customer"]`: offers,
contracts, deals, valuations) — and, in
a group of one, every contact of the group (one that predates provenance
too). Their contact channels, tags, contact groups, external ids, vehicle
links and legal bases come with them.

The group predicate below is the Betrieb's OWN group, handed in by platform —
never another's: `tests/architecture/test_no_ambient_group_read.py` lists
this file for that reason.
"""

from collections.abc import Mapping, Sequence

from sqlalchemy import or_, select

from app.core.export import ExportedTable, ExportScope, TableExporter, table_of
from app.contact.models.contact import (
    Contact,
    ContactAddress,
    ContactGroupAssignment,
    ContactEmail,
    ContactExternalId,
    ContactNumberSequence,
    ContactPhone,
    ContactRoleAssignment,
    ContactTag,
)
from app.contact.models.legal_basis import LegalBasis
from app.contact.models.stock_item_booking import StockItemBooking
from app.contact.models.vehicle_party import VehicleParty

_contact = table_of(Contact)
_booking = table_of(StockItemBooking)


def _related_contact_ids(scope: ExportScope):
    """The ids of the contacts in the Betrieb's relationship."""

    own_group = _contact.c.group_id == scope.group_id
    if scope.group_is_single:
        return select(_contact.c.id).where(own_group)
    return select(_contact.c.id).where(
        own_group,
        or_(
            _contact.c.dealership_id == scope.dealership_id,
            _contact.c.id.in_(sorted(scope.referenced("customer"))),
        ),
    )


def _child(model) -> ExportedTable:
    table = table_of(model)
    return ExportedTable(
        table=table,
        scope=lambda scope: table.c.contact_id.in_(_related_contact_ids(scope)),
    )


class ContactExporter(TableExporter):
    context = "customer"

    def exported_tables(self) -> Sequence[ExportedTable]:
        sequence = table_of(ContactNumberSequence)
        return (
            ExportedTable(
                table=_contact,
                scope=lambda scope: _contact.c.id.in_(_related_contact_ids(scope)),
            ),
            _child(ContactAddress),
            _child(ContactEmail),
            _child(ContactPhone),
            _child(ContactExternalId),
            _child(ContactTag),
            _child(ContactGroupAssignment),
            _child(ContactRoleAssignment),
            _child(VehicleParty),
            _child(LegalBasis),
            ExportedTable(table=_booking, scope=lambda scope: _booking.c.tenant_id == scope.dealership_id),
            ExportedTable(
                table=sequence,
                scope=lambda scope: sequence.c.group_id == scope.group_id,
                in_archive=False,
                exclusion_reason="The group's contact-number counter; the numbers are on the contacts.",
                marks_write=False,
            ),
        )

    def excluded_tables(self) -> Mapping[str, str]:
        return {}
