"""Registers every context's part of «copy configuration from a sister
Betrieb» with `app.core.configuration_copy` (KAN-818; FR-A-01). A composition
root directly under app/, like app.ownership_registry: platform runs the copy
and asks the registry, importing no context above it.

Imported by app.main (the copy is an API action). Importing it twice is
harmless: the registry replaces by subset.
"""

from app.aftersales.public import ServiceCatalogueCopier
from app.core.configuration_copy import register_configuration_copier


def register_configuration_copiers() -> None:
    for copier in (ServiceCatalogueCopier(),):
        register_configuration_copier(copier)


register_configuration_copiers()
