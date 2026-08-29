"""The only surface other contexts may import from platform. Import-linter's
contract allows `app.<other-context>` to import `app.platform.public`, never
`app.platform.models` / `app.platform.services` / `app.platform.api` directly.
"""

from app.platform.models.dealership import DealerGroup, Dealership, Location
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
from app.platform.services.dealership import (
    get_dealership_default_correspondence_language,
    get_dealership_or_404,
    get_location_or_404,
)
from app.platform.services.document_render import render_document
from app.platform.services.reference_data import get_reference_list_or_404, get_reference_value_or_404
from app.platform.services.user import get_user_or_404

# WP-6b: Addressee/ContentDefinition/.../render_document are the shared
# document-render layer's public contract — every future module that
# renders a document (WP-7/8/9) builds a ContentDefinition out of these
# block types and calls render_document. SwissLanguage itself is imported
# straight from app.core.i18n, not re-exported here — app.core is already
# universally importable.
__all__ = [
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
    "ParagraphBlock",
    "SignatureBlock",
    "User",
    "get_dealership_default_correspondence_language",
    "get_dealership_or_404",
    "get_location_or_404",
    "get_reference_list_or_404",
    "get_reference_value_or_404",
    "get_user_or_404",
    "render_document",
]
