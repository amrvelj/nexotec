"""The only surface other contexts may import from platform. Import-linter's
contract allows `app.<other-context>` to import `app.platform.public`, never
`app.platform.models` / `app.platform.services` / `app.platform.api` directly.
"""

from app.platform.administration.api.labour_rates import create_labour_rate_agreement
from app.platform.administration.api.labour_rates import guard as labour_rates_guard
from app.platform.administration.schemas.labour_rates import LabourRateAgreementCreate, LabourRateAgreementRead
from app.platform.administration.services.data_export_tables import PlatformExporter
from app.platform.administration.services.location_channel_facts import (
    ChannelOpeningHours,
    LocationChannelFacts,
    closed_days_until,
    location_channel_facts,
)
from app.platform.administration.services.location_channel_images import (
    ChannelImage,
    LocationChannelImagery,
    location_channel_images,
    read_location_channel_image,
)
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
from app.platform.documents.services.document_links import (
    DocumentSource,
    IssuedDocumentLink,
    RenderedDocument,
    issue_document_link,
    register_document_source,
    sample_document_link_url,
)
from app.platform.documents.services.document_render import render_document
from app.platform.documents.services.message_types import (
    MessageChannel,
    MessageDelivery,
    MessagePurpose,
    MessageType,
    MessageTypeKind,
    MessageTypeRegistrationError,
    delivery_of,
    get_message_type,
    message_types,
    register_message_type,
    text_category_of,
)
from app.platform.documents.services.reconciliation_targets import document_link_reference_check
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
from app.platform.identity.services.user_labour_category import labour_categories_of_user
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
from app.platform.organisation.services.dealer_dealerships import is_platform_dealership
from app.platform.organisation.services.dealership import get_dealership_default_correspondence_language
from app.platform.organisation.services.entitlement_facts import MODULE_ENTITLEMENT_GRANTED_EVENT
from app.platform.organisation.services.group_read_dealerships import DealershipRef, list_group_read_dealerships
from app.platform.organisation.services.labour_rate_answer import (
    LabourCategoryLabel,
    LabourRateAnswer,
    LabourRateMissing,
    LabourRateSource,
    labour_categories_of_dealership,
    labour_category_labels,
    labour_rate_for,
)
from app.platform.organisation.services.labour_rate_settings import (
    LABOUR_RATE_SETTINGS,
    LabourTimeUnit,
    labour_time_unit,
)
from app.platform.organisation.services.location import LocationLabel, get_location_labels
from app.platform.organisation.services.number_range import allocate_number
from app.platform.organisation.services.organisation_refs import (
    DealershipSummary,
    LocationSummary,
    enable_group_read,
    get_dealer_group_or_404,
    get_dealership_cash_rounding,
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
    labour_rate_category_reference_check,
    location_reference_check,
)
from app.platform.organisation.services.setting_declarations import (
    SettingDeclaration,
    SettingDeclarationError,
    SettingKind,
    TextValue,
    WhenNotSet,
    check_declaration_references,
)
from app.platform.organisation.services.setting_declarations import register_setting as _register_setting
from app.platform.organisation.services.setting_store import SettingRead, read_setting
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
# flow (contact mail through the user's own Microsoft 365 or Google Workspace
# mailbox) records the grant here — link_connected_account and
# revoke_connected_account each commit their own transaction (ADR-047; the
# caller compensates a link it cannot finish with the revoke), and
# connected_account_states reads whether a grant is still live (GrantState:
# ids, provider, external account id and status — never the address or the
# reference). platform alone writes the row; the grant is never an identity.
#
# KAN-217 (FR-A-52, FR-A-53): the registry of contact message types the one
# send dialog serves. A module registers each document or reminder type it
# lets a user send — purpose (transactional | marketing), document or
# reminder, its standard-text code per channel — with register_message_type,
# called from the composition root app/message_type_registry.py (platform
# never imports the module). message_types / get_message_type read it, and
# delivery_of says whether a channel attaches the document or sends a secure
# link. A wrong or conflicting registration raises
# MessageTypeRegistrationError at start-up. Plain values; it writes nothing.
#
# KAN-217 PR-4 (ADR-193): secure document links. The module that registers a
# document type registers its DocumentSource (render one of its documents for
# a Betrieb, or None) with register_document_source, from the same composition
# root. issue_document_link writes and commits a 14-day link on platform's own
# session (ADR-204) and returns the raw token once (IssuedDocumentLink);
# sample_document_link_url is one of the same length, for checking a message
# before the link is issued. Only its SHA-256 is
# stored; the URL carries it in the fragment ({app}/d#<token>), never a path.
# Platform serves the SPA's GET /v1/document-links/current (token in a header).
#
# KAN-805 (ADR-214): the settings store. An area declares each setting it reads
# (SettingDeclaration: key `<area>.<name>`, kind, what «not set» does, whether it
# is a setup-checklist item) with register_setting, called from the composition
# root app/setting_registry.py (platform never imports the area); a wrong or
# conflicting declaration raises SettingDeclarationError at start-up. It reads a
# value with read_setting(db, dealership_id=, location_id=, key="<literal>"): the
# location's value where it has one, else the Betrieb's, else «not set» with what
# that acts as (SettingRead, plain values). The key is a literal so
# tests/architecture/test_setting_keys_declared_and_read.py can pair every read
# with its declaration. A plain read; it writes nothing.
# check_declaration_references runs after every area registered: a choice's
# `choice_requires` (KAN-806, O18 «work units» needs O19) names a declared key.
#
# KAN-806 (D-A-19): labour rates. labour_rate_for answers «the rate for
# category C at location L on day D for invoice recipient R»: an agreed rate
# first, else the category rate, else found=False — an answer, never an error
# or a default (LabourRateAnswer: the source, the row, the amount per hour as
# typed and whether it was typed net or with VAT). labour_categories_of_user
# gives the categories a user is allocated to; labour_time_unit gives O18/O19
# (hours, or work units with how many make an hour). The composition root
# registers the three settings (LABOUR_RATE_SETTINGS) with register_setting. Plain reads.
# The composition root's agreement POST (app/api/v1/labour_rate_agreements.py)
# calls labour_rates_guard, resolves the contact, then create_labour_rate_agreement
# (LabourRateAgreementCreate in, LabourRateAgreementRead out; it commits).
#
# KAN-810 (Aftersales' workshop order): labour_category_labels names a labour
# position's category beside its id (LabourCategoryLabel, plain values);
# get_dealership_cash_rounding is the step the order's total rounds to;
# labour_rate_category_reference_check is the category reference's nightly
# check (reconciliation only). Plain reads.
#
# KAN-818 (the service catalogue): labour_categories_of_dealership lists a
# Betrieb's categories, so the catalogue's copy from a sister Betrieb can
# match a package's categories by name. A plain read.


# The settings store's door for declarations (see the paragraph above).
def register_setting(declaration: SettingDeclaration) -> None:
    """Declares a setting (KAN-805, ADR-214). Also refuses, at start-up, a
    checklist item blocking an action the setup checklist does not know: the
    store sits below the checklist and cannot judge it, and a wrong one found
    only when the checklist is derived would fail it for every Betrieb."""

    blocks = declaration.checklist_blocks
    if blocks is not None and blocks not in {action.value for action in SetupBlockedAction}:
        raise SettingDeclarationError(
            f"Setting {declaration.key!r} blocks {blocks!r}, no action of the setup checklist."
        )
    _register_setting(declaration)


__all__ = [
    "LABOUR_RATE_SETTINGS",
    "MODULE_ENTITLEMENT_GRANTED_EVENT",
    "PLACEHOLDER_UNRESOLVED",
    "ActingUser",
    "Addressee",
    "ChannelImage",
    "ChannelOpeningHours",
    "ConnectedAccountProvider",
    "ConnectedAccountStatus",
    "ContactGroupCodes",
    "ContentDefinition",
    "DealerGroupRead",
    "DealershipRef",
    "DealershipSummary",
    "DocumentLine",
    "DocumentSource",
    "DocumentWatermark",
    "GrantRevoker",
    "GrantState",
    "IssuedDocumentLink",
    "KeyValueBlock",
    "KeyValueRow",
    "LabourCategoryLabel",
    "LabourRateAgreementCreate",
    "LabourRateAgreementRead",
    "LabourRateAnswer",
    "LabourRateMissing",
    "LabourRateSource",
    "LabourTimeUnit",
    "LineItemsBlock",
    "LineStyle",
    "LinkedGrant",
    "LocationChannelFacts",
    "LocationChannelImagery",
    "LocationLabel",
    "LocationSummary",
    "MessageChannel",
    "MessageDelivery",
    "MessagePurpose",
    "MessageType",
    "MessageTypeKind",
    "MessageTypeRegistrationError",
    "NotificationEventCode",
    "NotificationSubject",
    "NumberRangeDocumentType",
    "ParagraphBlock",
    "Placeholder",
    "PlatformExporter",
    "PublishingMissingItem",
    "RenderedDocument",
    "RenderedStandardText",
    "RevokedGrant",
    "SettingDeclaration",
    "SettingDeclarationError",
    "SettingKind",
    "SettingRead",
    "SetupBlockedAction",
    "SignatureBlock",
    "StandardTextAppliesTo",
    "StandardTextCategory",
    "StandardTextSlot",
    "TextValue",
    "UnknownNotificationEvent",
    "UserDisplayLabel",
    "WhenNotSet",
    "actor_reference_checks",
    "allocate_number",
    "check_declaration_references",
    "closed_days_until",
    "connected_account_states",
    "contact_group_codes",
    "create_labour_rate_agreement",
    "dealer_group_reference_check",
    "dealership_exists",
    "dealership_group_of",
    "dealership_outside_group",
    "dealership_reference_check",
    "delivery_of",
    "document_link_reference_check",
    "enable_group_read",
    "find_dealer_group_ids",
    "find_dealership_group_ids",
    "get_active_reference_value_codes",
    "get_active_user_acting_in",
    "get_dealer_group_or_404",
    "get_dealership_cash_rounding",
    "get_dealership_default_correspondence_language",
    "get_dealership_or_404",
    "get_group_display_labels",
    "get_location_labels",
    "get_location_or_404",
    "get_message_type",
    "get_offboarded_audit_attribution",
    "get_primary_location_id",
    "get_user_display_labels",
    "is_platform_dealership",
    "issue_document_link",
    "labour_categories_of_dealership",
    "labour_categories_of_user",
    "labour_category_labels",
    "labour_rate_category_reference_check",
    "labour_rate_for",
    "labour_rates_guard",
    "labour_time_unit",
    "link_connected_account",
    "list_active_users_acting_in",
    "list_dealer_manager_emails",
    "list_group_read_dealerships",
    "list_user_dealer_group_ids",
    "list_user_dealership_ids",
    "location_channel_facts",
    "location_channel_images",
    "location_reference_check",
    "message_types",
    "notify",
    "publishing_profile_missing_items",
    "publishing_profile_ready",
    "read_location_channel_image",
    "read_setting",
    "register_document_source",
    "register_grant_revoker",
    "register_message_type",
    "register_setting",
    "render_document",
    "render_standard_text",
    "require_setup_item_for",
    "revoke_connected_account",
    "sample_document_link_url",
    "text_category_of",
    "ui_language_of",
    "unregister_grant_revoker",
    "user_is_deactivated",
    "user_reference_check",
]
