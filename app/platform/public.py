"""The only surface other contexts may import from platform. Import-linter's
contract allows `app.<other-context>` to import `app.platform.public`, never
`app.platform.models` / `app.platform.services` / `app.platform.api` directly.
"""

from app.platform.models.dealership import DealerGroup, Dealership, Location
from app.platform.models.number_range import NumberRangeDocumentType
from app.platform.models.standard_text import StandardTextAppliesTo, StandardTextSlot
from app.platform.models.standard_text_delegation import StandardTextCategory
from app.platform.models.user import User
from app.platform.reference.services.reference_data import get_active_reference_value_codes
from app.platform.schemas.dealership import DealerGroupRead
from app.platform.schemas.document_content import (
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
from app.platform.services.actor_reference import actor_reference_checks
from app.platform.services.connected_account import (
    ConnectedAccountProvider,
    GrantRevoker,
    RevokedGrant,
    register_grant_revoker,
    unregister_grant_revoker,
)
from app.platform.services.contact_group import ContactGroupCodes, contact_group_codes
from app.platform.services.data_export_tables import PlatformExporter
from app.platform.services.dealership import (
    enable_group_read,
    get_dealer_group_or_404,
    get_dealership_default_correspondence_language,
    get_dealership_or_404,
)
from app.platform.services.document_render import render_document
from app.platform.services.group_labels import get_group_display_labels
from app.platform.services.group_read_dealerships import DealershipRef, list_group_read_dealerships
from app.platform.services.location import get_location_or_404
from app.platform.services.notification_catalogue import NotificationEventCode
from app.platform.services.notifications import NotificationSubject, UnknownNotificationEvent, notify
from app.platform.services.number_range import allocate_number
from app.platform.services.standard_text import RenderedStandardText, render_standard_text
from app.platform.services.standard_text_placeholders import PLACEHOLDER_UNRESOLVED, Placeholder
from app.platform.services.user_preference import ui_language_of
from app.platform.services.user_reads import (
    UserDisplayLabel,
    get_active_user_acting_in,
    get_user_display_labels,
    list_active_users_acting_in,
    list_dealer_manager_emails,
    list_user_dealer_group_ids,
    list_user_dealership_ids,
)

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
# per process. Registering writes nothing; nothing here lets another
# context read or write a connected account.
__all__ = [
    "PLACEHOLDER_UNRESOLVED",
    "Addressee",
    "ConnectedAccountProvider",
    "ContactGroupCodes",
    "ContentDefinition",
    "DealerGroup",
    "DealerGroupRead",
    "Dealership",
    "DealershipRef",
    "DocumentLine",
    "DocumentWatermark",
    "GrantRevoker",
    "KeyValueBlock",
    "KeyValueRow",
    "LineItemsBlock",
    "LineStyle",
    "Location",
    "NotificationEventCode",
    "NotificationSubject",
    "NumberRangeDocumentType",
    "ParagraphBlock",
    "Placeholder",
    "PlatformExporter",
    "RenderedStandardText",
    "RevokedGrant",
    "SignatureBlock",
    "StandardTextAppliesTo",
    "StandardTextCategory",
    "StandardTextSlot",
    "UnknownNotificationEvent",
    "User",
    "UserDisplayLabel",
    "actor_reference_checks",
    "allocate_number",
    "contact_group_codes",
    "enable_group_read",
    "get_active_reference_value_codes",
    "get_active_user_acting_in",
    "get_dealer_group_or_404",
    "get_dealership_default_correspondence_language",
    "get_dealership_or_404",
    "get_group_display_labels",
    "get_location_or_404",
    "get_user_display_labels",
    "list_active_users_acting_in",
    "list_dealer_manager_emails",
    "list_group_read_dealerships",
    "list_user_dealer_group_ids",
    "list_user_dealership_ids",
    "notify",
    "register_grant_revoker",
    "render_document",
    "render_standard_text",
    "ui_language_of",
    "unregister_grant_revoker",
]
