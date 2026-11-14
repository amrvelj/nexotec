from app.core.permissions import ModuleCode
from app.core.schemas import CamelModel


class EntitlementsRead(CamelModel):
    """The modules the Betrieb is licensed for (D-A-04): the manager reads
    "what we bought", platform_admin writes. `entitlements_version` is the
    value sessions are minted against; `dealership_version` is the If-Match
    value for a PUT."""

    modules: list[ModuleCode]
    entitlements_version: int
    dealership_version: int


class EntitlementsUpdate(CamelModel):
    """Full replacement of the set — the modules not listed are withdrawn."""

    modules: list[ModuleCode]
