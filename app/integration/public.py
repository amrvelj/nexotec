"""The only surface other contexts may import from `app.integration`.
Import-linter's contract allows `app.<other-context>` to import
`app.integration.public`, never `.models`/`.services`/`.api`/`.adapters`
directly.

Deliberately absent: anything that could return a secret value.
`resolve_secret`/`create_secret`/`update_secret`/`delete_secret` (services/
secrets_backend.py) are never exported here — only the adapters (PR-2/3),
which already live inside this context, may call them.

`call_capability` (PR-2) is the one way `app.vehicle` (PR-4) ever reaches
a provider: it resolves the connection's own adapter, times the call, and
writes exactly one `integration_call_log` row, success or failure. The
adapter data shapes (`VariantMasterData` etc.) are exported alongside it
purely as return-type vocabulary for PR-4's own type hints — they carry
no provider code in any field (the reading rule: "application code never
sees a provider code"), so exporting them is not exporting anything
provider-specific.

`get_entitlement` lands here once PR-5 exists.
"""

from app.integration.adapters.base import (
    ForecastResult,
    ProviderAdapter,
    SystemWatermark,
    ValuationResult,
    VariantColourData,
    VariantImageData,
    VariantMasterData,
    VariantOptionData,
    VariantTyreSpecData,
)
from app.integration.models.connection import ConnectionStatus, IntegrationConnection
from app.integration.services.connections import get_enabled_connection, list_enabled_connection_tenant_ids_for_provider
from app.integration.services.gateway import (
    ConnectionDisabledError,
    ProviderGatewayError,
    UnknownProviderError,
    call_capability,
)

__all__ = [
    "ConnectionDisabledError",
    "ConnectionStatus",
    "ForecastResult",
    "IntegrationConnection",
    "ProviderAdapter",
    "ProviderGatewayError",
    "SystemWatermark",
    "UnknownProviderError",
    "ValuationResult",
    "VariantColourData",
    "VariantImageData",
    "VariantMasterData",
    "VariantOptionData",
    "VariantTyreSpecData",
    "call_capability",
    "get_enabled_connection",
    "list_enabled_connection_tenant_ids_for_provider",
]
