"""Planning's export declaration (KAN-218; FR-A-44): the Betrieb's planner
lists and resources with their qualifications and working hours, and its
bookings with the resources each holds and the warnings overruled. Every
planning table carries `tenant_id` (ADR-221 T7), so each is scoped by it."""

from collections.abc import Sequence

from app.core.export import ExportedTable, TableExporter, table_of
from app.planning.models.booking import Booking, BookingResource, OverruledWarning
from app.planning.models.lists import Qualification, TechnicianType, WorkplaceType
from app.planning.models.resource import Resource, ResourceQualification, WorkingHours


def _tenant(model) -> ExportedTable:
    table = table_of(model)
    return ExportedTable(table=table, scope=lambda scope: table.c.tenant_id == scope.dealership_id)


class PlanningExporter(TableExporter):
    context = "planning"

    def exported_tables(self) -> Sequence[ExportedTable]:
        return (
            _tenant(WorkplaceType),
            _tenant(TechnicianType),
            _tenant(Qualification),
            _tenant(Resource),
            _tenant(ResourceQualification),
            _tenant(WorkingHours),
            _tenant(Booking),
            _tenant(BookingResource),
            _tenant(OverruledWarning),
        )
