"""The only surface other contexts may import from contact. Import-linter's
contract allows `app.<other-context>` to import `app.contact.public`, never
`app.contact.models` / `app.contact.services` / `app.contact.api` directly.

It hands out values, never a mapped class (KAN-123 step 5, ADR-162): a
contact is a `ContactRef`, a vehicle party a `VehiclePartyRef`; what another
context's reconciliation checks against `contact` is built here too
(`services/reconciliation_targets.py`).

`message_recipient` / `preselected_message_channel` (KAN-217) are the send
dialog's read of a contact: where a message on e-mail, SMS or WhatsApp goes
and Contacts' consent verdict for a transactional or marketing send (a
`MessageRecipient`, plain values, the refusal as a stable code), and the
channel the dialog opens on. SELECTs only.

`supplier_contact` (KAN-465) is Stock's purchase booking's read of the contact
it names as the supplier: its label, whether it holds the supplier role, and
the VAT-registration prefill. SELECTs only.
"""

from app.contact.models.contact import ContactLifecycleStatus
from app.contact.models.vehicle_party import VehiclePartyRole
from app.contact.ownership import ContactOwnershipProvider
from app.contact.schemas.contact import ContactVehiclePage
from app.contact.services.advisor_label import oldest_advisor_label_age_seconds, refresh_advisor_labels
from app.contact.services.contact import has_usable_domicile_address
from app.contact.services.contact_channels import channel_authorises_marketing
from app.contact.services.contact_refs import (
    ContactRef,
    VehiclePartyRef,
    allocate_vehicle_party,
    contact_display_name,
    get_contact_or_404,
    list_vehicle_party_holders,
)
from app.contact.services.data_export import ContactExporter
from app.contact.services.legal_basis import has_any_basis_for_group
from app.contact.services.message_recipient import (
    MessageRecipient,
    message_recipient,
    preselected_message_channel,
)
from app.contact.services.reconciliation_targets import contact_exists, contact_reference_check
from app.contact.services.supplier_contact import SupplierContact, supplier_contact
from app.contact.services.vehicle_page import StockItemsFor, contact_vehicle_page
from app.contact.services.vehicle_party import contact_merge_chain, resolve_live_contact_id
from app.contact.services.vehicle_party_labels import (
    oldest_vehicle_party_label_age_seconds,
    refresh_vehicle_party_labels,
)
from app.contact.services.vehicle_party_reads import (
    ContactSalesLabel,
    contact_in_group,
    contact_is_merged,
    get_contact_sales_labels,
)

__all__ = [
    "ContactExporter",
    "ContactLifecycleStatus",
    "ContactOwnershipProvider",
    "ContactRef",
    "ContactSalesLabel",
    "ContactVehiclePage",
    "MessageRecipient",
    "StockItemsFor",
    "SupplierContact",
    "VehiclePartyRef",
    "VehiclePartyRole",
    "allocate_vehicle_party",
    "channel_authorises_marketing",
    "contact_display_name",
    "contact_exists",
    "contact_in_group",
    "contact_is_merged",
    "contact_merge_chain",
    "contact_reference_check",
    "contact_vehicle_page",
    "get_contact_or_404",
    "get_contact_sales_labels",
    "has_any_basis_for_group",
    "has_usable_domicile_address",
    "list_vehicle_party_holders",
    "message_recipient",
    "oldest_advisor_label_age_seconds",
    "oldest_vehicle_party_label_age_seconds",
    "preselected_message_channel",
    "refresh_advisor_labels",
    "refresh_vehicle_party_labels",
    "resolve_live_contact_id",
    "supplier_contact",
]
