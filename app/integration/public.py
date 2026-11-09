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
writes one `integration_call_log` row per provider request the block sends
(KAN-486), success or failure — or the block's one row when it sent none. The
adapter data shapes (`VariantMasterData` etc.) are exported alongside it
purely as return-type vocabulary for PR-4's own type hints — they carry
no provider code in any field (the reading rule: "application code never
sees a provider code"), so exporting them is not exporting anything
provider-specific.

`get_entitlement` (PR-5) returns the raw `IntegrationEntitlement` row or
`None` — "never probed or declared" is a fact this registry reports, not
a policy it applies; `app.vehicle.services.catalogue_entitlements` is the
one place that decides what `None` should mean for a given screen.
`resolve_adapter` (KAN-27) is `call_capability` without the call-log/
circuit-breaker side effects — for a caller whose use of the adapter
makes no provider I/O (validating data shape locally). A real provider
call must always go through `call_capability` instead. `CircuitOpenError`
is exported alongside it purely so such a caller can still fail
gracefully when a connection's breaker is already open, without a real
call ever being attempted.

`PlateInfoData` / `BestMatchResult` and `vin_decode_granted` (KAN-42,
C-D) are what the identification waterfall in
`app.vehicle.services.identification` needs: the plate-record shape the
plate-lookup cache stores (once `KontrollschildInfo`, now what a Vehicle
Build record reduces to) and the `vehiclematch` return shape, and a
read-only check of a connection's `vin` licence option (KAN-549): the
row is written only by integration's own "Test connection" probes —
ADR-047 keeps that write out of another context's transaction.

KAN-547 / KAN-548: `BuiltVehicleData`, `VariantSearchPage` and the catalogue
browse shapes are the identAPI operations' return vocabulary;
`get_captions` reads the provider caption store
(`services/captions.py`, written by "Test connection") as plain data —
`app.vehicle` shows a provider's wording beside an unmapped code, never
copies it. `ProviderNotEntitledError` and `ProviderTimeoutError` are
exported so the identification waterfall can tell «not in the contract»
from «unavailable» without importing the errors module.
"""

from app.integration.adapters.base import (
    BestMatchResult,
    BuiltVehicleData,
    CaptionData,
    CatalogueBrandData,
    CatalogueModelGroupData,
    EnergyLabelData,
    OptionPriceWithData,
    PlateInfoData,
    ProviderAdapter,
    VariantColourData,
    VariantColoursData,
    VariantImageData,
    VariantMasterData,
    VariantOptionData,
    VariantSearchPage,
    VariantTyreSpecData,
    VehiclePriceData,
)
from app.integration.adapters.marketplace_base import (
    MarketplaceAdapter,
    MarketplaceListing,
    MarketplaceListingError,
    MarketplaceTransmissionError,
    TransmissionResult,
)
from app.integration.errors import (
    ProviderConfigurationError,
    ProviderGatewayError,
    ProviderNotEntitledError,
    ProviderRequestError,
    ProviderTimeoutError,
)
from app.integration.models.connection import ConnectionStatus, IntegrationConnection
from app.integration.models.entitlement import IntegrationEntitlement
from app.integration.services.captions import ProviderCaption, get_captions
from app.integration.services.connections import (
    get_enabled_connection,
    get_entitlement,
    valuation_granted,
    vin_decode_granted,
)
from app.integration.services.gateway import (
    ConnectionDisabledError,
    UnknownProviderError,
    call_capability,
    resolve_adapter,
)
from app.integration.services.resilience import CircuitOpenError

__all__ = [
    "BestMatchResult",
    "BuiltVehicleData",
    "CaptionData",
    "CatalogueBrandData",
    "CatalogueModelGroupData",
    "CircuitOpenError",
    "ConnectionDisabledError",
    "ConnectionStatus",
    "EnergyLabelData",
    "IntegrationConnection",
    "IntegrationEntitlement",
    "MarketplaceAdapter",
    "MarketplaceListing",
    "MarketplaceListingError",
    "MarketplaceTransmissionError",
    "OptionPriceWithData",
    "PlateInfoData",
    "ProviderAdapter",
    "ProviderCaption",
    "ProviderConfigurationError",
    "ProviderGatewayError",
    "ProviderNotEntitledError",
    "ProviderRequestError",
    "ProviderTimeoutError",
    "TransmissionResult",
    "UnknownProviderError",
    "VariantColourData",
    "VariantColoursData",
    "VariantImageData",
    "VariantMasterData",
    "VariantOptionData",
    "VariantSearchPage",
    "VariantTyreSpecData",
    "VehiclePriceData",
    "call_capability",
    "get_captions",
    "get_enabled_connection",
    "get_entitlement",
    "resolve_adapter",
    "valuation_granted",
    "vin_decode_granted",
]
