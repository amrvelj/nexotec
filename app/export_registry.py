"""Registers every context's exporter with `app.core.export` (KAN-218;
Dealer Administration FR-A-44…46). A composition root directly under app/,
like app.ownership_registry: the one place that may name every context's
exporter, so that platform's export job asks the registry and imports no
context, and no context imports platform.

The stub contexts register a `StubExporter`: the registry is the complete
inventory of contexts, and a first table there fails the completeness test
(`tests/test_data_export_completeness.py`) until its exporter declares it.

Imported by app.main (the API: offboarding asks every exporter for the
Betrieb's last write) and app.worker (the export job runs there). Importing
it twice is harmless: the registry replaces by context.
"""

from app.core.export import StubExporter, register_exporter
from app.core.export_core import CoreExporter
from app.customer.public import CustomerExporter
from app.integration.public import IntegrationExporter
from app.inventory.public import InventoryExporter
from app.platform.public import PlatformExporter
from app.sales.public import SalesExporter
from app.valuation.public import ValuationExporter
from app.vehicle.public import VehicleExporter

STUB_CONTEXTS = ("aftersales", "parts", "finance", "reporting", "compliance")


def register_exporters() -> None:
    for exporter in (
        CoreExporter(),
        PlatformExporter(),
        IntegrationExporter(),
        VehicleExporter(),
        CustomerExporter(),
        ValuationExporter(),
        InventoryExporter(),
        SalesExporter(),
        *(StubExporter(context) for context in STUB_CONTEXTS),
    ):
        register_exporter(exporter)


register_exporters()
