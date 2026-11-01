"""The only surface other contexts may import from platform. Import-linter's
contract allows `app.<other-context>` to import `app.platform.public`, never
`app.platform.models` / `app.platform.services` / `app.platform.api` directly.
"""

from app.platform.models.dealership import DealerGroup, Dealership, Location
from app.platform.models.standard_text import StandardTextAppliesTo, StandardTextSlot
from app.platform.models.standard_text_delegation import StandardTextCategory
from app.platform.models.user import User
from app.platform.schemas.document_content import (
    Addressee,
    ContentDefinition,
    DocumentLine,
    KeyValueBlock,
    KeyValueRow,
    LineItemsBlock,
    LineStyle,
    ParagraphBlock,
    SignatureBlock,
)
from app.platform.services.actor_reference import actor_reference_checks
from app.platform.services.dealership import (
    get_dealership_default_correspondence_language,
    get_dealership_or_404,
    get_location_or_404,
)
from app.platform.services.document_render import render_document
from app.platform.services.group_labels import get_group_display_labels
from app.platform.services.notification_catalogue import NotificationEventCode
from app.platform.services.notifications import NotificationSubject, UnknownNotificationEvent, notify
from app.platform.services.reference_data import get_active_reference_value_codes
from app.platform.services.standard_text import RenderedStandardText, render_standard_text
from app.platform.services.standard_text_placeholders import PLACEHOLDER_UNRESOLVED, Placeholder
from app.platform.services.user import (
    UserDisplayLabel,
    get_active_user_acting_in,
    get_user_display_labels,
    get_user_or_404,
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
# universally importable.
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
__all__ = [
    "PLACEHOLDER_UNRESOLVED",
    "Addressee",
    "ContentDefinition",
    "DealerGroup",
    "Dealership",
    "DocumentLine",
    "KeyValueBlock",
    "KeyValueRow",
    "LineItemsBlock",
    "LineStyle",
    "Location",
    "NotificationEventCode",
    "NotificationSubject",
    "ParagraphBlock",
    "Placeholder",
    "RenderedStandardText",
    "SignatureBlock",
    "StandardTextAppliesTo",
    "StandardTextCategory",
    "StandardTextSlot",
    "UnknownNotificationEvent",
    "User",
    "UserDisplayLabel",
    "actor_reference_checks",
    "get_active_reference_value_codes",
    "get_active_user_acting_in",
    "get_dealership_default_correspondence_language",
    "get_dealership_or_404",
    "get_group_display_labels",
    "get_location_or_404",
    "get_user_display_labels",
    "get_user_or_404",
    "list_active_users_acting_in",
    "list_dealer_manager_emails",
    "list_user_dealer_group_ids",
    "list_user_dealership_ids",
    "notify",
    "render_document",
    "render_standard_text",
]
