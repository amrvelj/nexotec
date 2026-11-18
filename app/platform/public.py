"""The only surface other contexts may import from platform. Import-linter's
contract allows `app.<other-context>` to import `app.platform.public`, never
`app.platform.models` / `app.platform.services` / `app.platform.api` directly.
"""

from app.platform.administration.services.data_export_tables import PlatformExporter
from app.platform.administration.services.publishing_readiness import (
    PublishingMissingItem,
    publishing_profile_missing_items,
    publishing_profile_ready,
)
from app.platform.administration.services.setup_checklist import BlockedAction as SetupBlockedAction
from app.platform.administration.services.setup_checklist import require_item_for as require_setup_item_for
from app.platform.documents.models.standard_text import StandardTextAppliesTo, StandardTextSlot
from app.platform.documents.schemas.document_content import (
    Addressee,
    ContentDefinition,
    DocumentLine,
    DocumentWatermark,
    KeyValueBlock,
    KeyValueRow,
    LineItemsBlock,
    LineStyle,
    ParagraphBlock,
    SignatureBlock,
)
from app.platform.documents.services.document_render import render_document
from app.platform.documents.services.standard_text import RenderedStandardText, render_standard_text
from app.platform.documents.services.standard_text_placeholders import PLACEHOLDER_UNRESOLVED, Placeholder
from app.platform.identity.models.connected_account import ConnectedAccountStatus
from app.platform.identity.models.standard_text_delegation import StandardTextCategory
from app.platform.identity.services.actor_reference import actor_reference_checks
from app.platform.identity.services.audit_attribution import get_offboarded_audit_attribution
from app.platform.identity.services.connected_account import (
    ConnectedAccountProvider,
    GrantRevoker,
    GrantState,
    LinkedGrant,
    RevokedGrant,
    connected_account_states,
    link_connected_account,
    register_grant_revoker,
    revoke_connected_account,
    unregister_grant_revoker,
)
from app.platform.identity.services.group_labels import get_group_display_labels
from app.platform.identity.services.identity_refs import (
    ActingUser,
    get_active_user_acting_in,
    list_active_users_acting_in,
)
from app.platform.identity.services.notification_catalogue import NotificationEventCode
from app.platform.identity.services.notifications import NotificationSubject, UnknownNotificationEvent, notify
from app.platform.identity.services.reconciliation_targets import user_is_deactivated, user_reference_check
from app.platform.identity.services.user_location import get_primary_location_id
from app.platform.identity.services.user_preference import ui_language_of
from app.platform.identity.services.user_reads import (
    UserDisplayLabel,
    get_user_display_labels,
    list_dealer_manager_emails,
    list_user_dealer_group_ids,
    list_user_dealership_ids,
)
from app.platform.organisation.models.number_range import NumberRangeDocumentType
from app.platform.organisation.schemas.dealership import DealerGroupRead
from app.platform.organisation.services.contact_group import ContactGroupCodes, contact_group_codes
from app.platform.organisation.services.dealership import get_dealership_default_correspondence_language
from app.platform.organisation.services.group_read_dealerships import DealershipRef, list_group_read_dealerships
from app.platform.organisation.services.location import LocationLabel, get_location_labels
from app.platform.organisation.services.number_range import allocate_number
from app.platform.organisation.services.organisation_refs import (
    DealershipSummary,
    LocationSummary,
    enable_group_read,
    get_dealer_group_or_404,
    get_dealership_or_404,
    get_location_or_404,
)
from app.platform.organisation.services.reconciliation_targets import (
    dealer_group_reference_check,
    dealership_exists,
    dealership_group_of,
    dealership_outside_group,
    dealership_reference_check,
    find_dealer_group_ids,
    find_dealership_group_ids,
    location_reference_check,
)
from app.platform.reference.services.reference_data import get_active_reference_value_codes

# WP-6b: Addressee/ContentDefinition/.../render_document are the shared
# document-render layer's public contract — every future module that
# renders a document (WP-7/8/9) builds a ContentDefinition out of these
# block types and calls render_document. SwissLanguage itself is imported
# straight from app.core.i18n, not re-exported here — app.core is already
# universally importable. KAN-206: a SignatureBlock names its signers'
# user ids (their images are read at render time) and render_document takes
# a DocumentWatermark (DRAFT on a draft document); the letterhead, envelope
# window and footer columns are platform's alone.
#
# KAN-208: render_standard_text is the standard-text resolver every module
# prints its wording through (language → Betrieb default → DE; location →
# dealership → seed), with the closed Placeholder set it fills from the
# caller's values; an unresolvable token is a 422 carrying
# PLACEHOLDER_UNRESOLVED (A-RULE-4).
# KAN-216 (D-A-40, FR-A-47): notify / NotificationEventCode /
# NotificationSubject are the staff-notification catalogue's entry point.
# notify(db, …) writes platform's own tables (the inbox row and its outbox
# row) in the session it is given, so today it is PLATFORM-INTERNAL: every
# caller is platform's own security family. Called from another context it
# would write platform's tables inside that context's transaction — the
# shared transaction rule 12 / ADR-047 forbid. The first module to raise a
# notification needs a decided design and an ADR first — e.g. its own outbox
# event that platform consumes, or a call on a session of its own with a
# compensating action — and that design classified in
# tests/architecture/test_adr_047_no_shared_transaction.py, which refuses an
# unclassified cross-context call to notify by name. Whatever the design, the
# module registers its codes in the catalogue (the declaration and a platform
# migration) in the same PR. An unknown code raises UnknownNotificationEvent.
# Never an e-mail.
#
# KAN-225 (FR-A-05): publishing_profile_missing_items is what a channel the
# profile feeds lacks, as stable codes in the dealer's words — Stock asks it
# before a car is published to the Betrieb's website, so a missing Impressum
# is refused before the listing exists. publishing_profile_ready is the yes/no
# the setup checklist (KAN-214) reads for its publishing-profile item once
# that item joins it. Both are plain reads; neither writes.
#
# KAN-412 (FR-AU-24): register_grant_revoker is how the integration that
# links a provider's accounts (the Outlook integration, Microsoft 365) ends
# a revoked grant at the provider. The revoker receives a RevokedGrant —
# plain data (id, provider, external account id, secret reference), never
# platform's row — from platform's own consumer after the revoke committed,
# at least once: it must end the grant and drop the secret, succeed again
# on a grant already ended, and raise on a refusal (the outbox retries,
# then dead-letters; ADR-118). Its exception message is stored in the outbox
# row and logged, so it must never carry token material. Register it in the
# worker process (app/worker.py), where the consumer runs: the registry is
# per process. Registering writes nothing.
#
# KAN-215 (FR-AU-23, ADR-118): the integration that runs a provider's link
# flow (customer mail through the user's own Microsoft 365 or Google Workspace
# mailbox) records the grant here — link_connected_account and
# revoke_connected_account each commit their own transaction (ADR-047; the
# caller compensates a link it cannot finish with the revoke), and
# connected_account_states reads whether a grant is still live (GrantState:
# ids, provider, external account id and status — never the address or the
# reference). platform alone writes the row; the grant is never an identity.
__all__ = [
    "PLACEHOLDER_UNRESOLVED",
    "ActingUser",
    "Addressee",
    "ConnectedAccountProvider",
    "ConnectedAccountStatus",
    "ContactGroupCodes",
    "ContentDefinition",
    "DealerGroupRead",
    "DealershipRef",
    "DealershipSummary",
    "DocumentLine",
    "DocumentWatermark",
    "GrantRevoker",
    "GrantState",
    "KeyValueBlock",
    "KeyValueRow",
    "LineItemsBlock",
    "LineStyle",
    "LinkedGrant",
    "LocationLabel",
    "LocationSummary",
    "NotificationEventCode",
    "NotificationSubject",
    "NumberRangeDocumentType",
    "ParagraphBlock",
    "Placeholder",
    "PlatformExporter",
    "PublishingMissingItem",
    "RenderedStandardText",
    "RevokedGrant",
    "SetupBlockedAction",
    "SignatureBlock",
    "StandardTextAppliesTo",
    "StandardTextCategory",
    "StandardTextSlot",
    "UnknownNotificationEvent",
    "UserDisplayLabel",
    "actor_reference_checks",
    "allocate_number",
    "connected_account_states",
    "contact_group_codes",
    "dealer_group_reference_check",
    "dealership_exists",
    "dealership_group_of",
    "dealership_outside_group",
    "dealership_reference_check",
    "enable_group_read",
    "find_dealer_group_ids",
    "find_dealership_group_ids",
    "get_active_reference_value_codes",
    "get_active_user_acting_in",
    "get_dealer_group_or_404",
    "get_dealership_default_correspondence_language",
    "get_dealership_or_404",
    "get_group_display_labels",
    "get_location_labels",
    "get_location_or_404",
    "get_offboarded_audit_attribution",
    "get_primary_location_id",
    "get_user_display_labels",
    "link_connected_account",
    "list_active_users_acting_in",
    "list_dealer_manager_emails",
    "list_group_read_dealerships",
    "list_user_dealer_group_ids",
    "list_user_dealership_ids",
    "location_reference_check",
    "notify",
    "publishing_profile_missing_items",
    "publishing_profile_ready",
    "register_grant_revoker",
    "render_document",
    "render_standard_text",
    "require_setup_item_for",
    "revoke_connected_account",
    "ui_language_of",
    "unregister_grant_revoker",
    "user_is_deactivated",
    "user_reference_check",
]
