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

`get_entitlement` (PR-5) returns an `EntitlementState` (granted, checked at)
or `None` — "never probed or declared" is a fact this registry reports, not
a policy it applies; `app.vehicle.services.catalogue_entitlements` is the
one place that decides what `None` should mean for a given screen.

`IntegrationStatusProvider` (KAN-223) is the connections' contribution to the
Betrieb's status page, `GbpStatusProvider` (KAN-776) the Business Profile
item of a Betrieb with a live binding, `GbpDriftStatusProvider` (KAN-226 PR-4)
one drift item per checked binding; all registered with `app.core.status` by the composition
root `app/status_registry.py` — platform never imports it.

`refresh_location_labels` / `oldest_location_label_age_seconds` (KAN-222)
are the nightly refresh of a connection's copy of its location's name and its
age gauge, which the worker registers and records like every label refresh.

No mapped class leaves through this door (KAN-123): `get_enabled_connection`
answers a `ConnectionRef` (the connection's id and its Betrieb), which
`call_capability` and `resolve_adapter` take back, refusing a row of another
Betrieb like a deleted one, and the registry's rows stay inside this context
(`services/connection_refs.py`). The licence reads (`get_entitlement`,
`vin_decode_granted`, `valuation_granted`) take the reference too and answer for
another Betrieb's or a platform-wide connection exactly as for an unknown one
(KAN-711). `ConnectionStatus` is an enum, not a row.

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

`generate` (KAN-227, D-A-33) is the one door to the Betrieb's own AI provider:
it resolves the AI connection, the prompt of a registry code (the Betrieb's
adaptation over Nexotec's seed, in the language chain) and the caller's
variables, sends one completion through `call_capability`, and returns a
`GeneratedText` — a suggestion the caller shows for the user to edit, stored
and published by nothing. Its refusals are the core error taxonomy (409
`ai_not_connected`, 422 `ai_provider_unavailable`), so a caller needs no
integration error type. `AI_PROMPT_VEHICLE_LISTING` names the one v1 code.

`deliver_message` (KAN-217 PR-5, ADR-204; A-RULE-9, A-RULE-10) is the one door
a contact message leaves through, on e-mail, SMS and WhatsApp. It takes the
consent verdict the caller read from `contact.public.message_recipient` and
refuses without it (409 `recipient_refused`); e-mail leaves from the author's
own connected mailbox, else the Betrieb's shared one, else a 409 naming both
connection screens (`resolve_sender`, KAN-215: `no_mailbox_connected`,
`own_mailbox_in_error`, `business_mailbox_in_error`); SMS and WhatsApp from
the Betrieb's own provider account (409 `messaging_not_connected`), WhatsApp
as an approved template only (409 `whatsapp_template_not_approved`). Each send
writes one delivery row on integration's own session (ADR-047) and answers a
`DeliveryRecord` — plain values; it never raises after its refusals. The mail
send alone (`send_contact_mail`) is no longer handed out: nothing above
integration sends without the verdict. `register_mail_grant_revokers`
is the worker's wiring for ending a revoked mailbox grant (ADR-118).
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
from app.integration.adapters.mail_base import MailAttachment
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
from app.integration.models.connection import ConnectionStatus
from app.integration.models.mail import DeliveryChannel, DeliveryStatus, SendingIdentityKind
from app.integration.services import gbp_photo_runner
from app.integration.services.ai_generation import GeneratedText, generate
from app.integration.services.ai_prompt_registry import VEHICLE_LISTING as AI_PROMPT_VEHICLE_LISTING
from app.integration.services.captions import ProviderCaption, get_captions
from app.integration.services.connection_refs import (
    ConnectionRef,
    EntitlementState,
    get_enabled_connection,
    get_entitlement,
    valuation_granted,
    vin_decode_granted,
)
from app.integration.services.connections import MARKETPLACE_PUBLISH_CAPABILITY
from app.integration.services.data_export import IntegrationExporter
from app.integration.services.gateway import (
    ConnectionDisabledError,
    UnknownProviderError,
    call_capability,
    resolve_adapter,
)
from app.integration.services.gbp_status import GbpDriftStatusProvider, GbpPhotoStatusProvider, GbpStatusProvider
from app.integration.services.location_labels import oldest_location_label_age_seconds, refresh_location_labels
from app.integration.services.mail_sending import (
    DeliveryRecord,
    ResolvedSender,
    resolve_sender,
)
from app.integration.services.mail_sending import register_revokers as register_mail_grant_revokers
from app.integration.services.message_delivery import (
    WhatsAppTemplate,
    check_deliverable,
    deliver_message,
    refuse_unless_allowed,
)
from app.integration.services.reconciliation_sources import message_delivery_recipient_reference_check
from app.integration.services.resilience import CircuitOpenError
from app.integration.services.send_readiness import (
    ChannelReadiness,
    DeliverySummary,
    channel_readiness,
    latest_deliveries,
)
from app.integration.services.status import IntegrationStatusProvider

# KAN-784 (ADR-209): the web service uploads the Business Profile photos,
# beside the object store's disk; app.main starts and stops it.
start_business_profile_photo_runner = gbp_photo_runner.start
stop_business_profile_photo_runner = gbp_photo_runner.stop

__all__ = [
    "AI_PROMPT_VEHICLE_LISTING",
    "MARKETPLACE_PUBLISH_CAPABILITY",
    "BestMatchResult",
    "BuiltVehicleData",
    "CaptionData",
    "CatalogueBrandData",
    "CatalogueModelGroupData",
    "ChannelReadiness",
    "CircuitOpenError",
    "ConnectionDisabledError",
    "ConnectionRef",
    "ConnectionStatus",
    "DeliveryChannel",
    "DeliveryRecord",
    "DeliveryStatus",
    "DeliverySummary",
    "EnergyLabelData",
    "EntitlementState",
    "GbpDriftStatusProvider",
    "GbpPhotoStatusProvider",
    "GbpStatusProvider",
    "GeneratedText",
    "IntegrationExporter",
    "IntegrationStatusProvider",
    "MailAttachment",
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
    "ResolvedSender",
    "SendingIdentityKind",
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
    "WhatsAppTemplate",
    "call_capability",
    "channel_readiness",
    "check_deliverable",
    "deliver_message",
    "generate",
    "get_captions",
    "get_enabled_connection",
    "get_entitlement",
    "latest_deliveries",
    "message_delivery_recipient_reference_check",
    "oldest_location_label_age_seconds",
    "refresh_location_labels",
    "refuse_unless_allowed",
    "register_mail_grant_revokers",
    "resolve_adapter",
    "resolve_sender",
    "start_business_profile_photo_runner",
    "stop_business_profile_photo_runner",
    "valuation_granted",
    "vin_decode_granted",
]
