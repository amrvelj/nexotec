"""The recipient of a contact message and the consent verdict for it
(KAN-217 PR-2; Dealer Administration FR-A-52, FR-A-53; PRD-Contacts FR-23,
D-17, D-21; ADR-067).

The send dialog's service (Sales' send route, a later PR) asks Contacts —
never its own copy of the contact — where a message on one channel goes and
whether it may go there. Contacts owns `preferred_channel` and the consent
per contact row, so the verdict is decided here, once, and the caller only
reads it (A-RULE-10: consent is never stubbed).

**The address per channel** is the read-model projection's row (ADR-067):
e-mail → the usable personal address, else the usable work address; SMS and
WhatsApp → the usable mobile number (there is no `sms` preferred channel; SMS
goes to the primary mobile). «Usable» is the one definition FR-03 and the
projections use: not closed, not «nicht verwenden».

**The verdict**, in this order — the first that fails is the reason:

1. a merged contact is refused (`contact_merged`); the caller follows the
   merge through `contact.merged`, never sends to the absorbed record;
2. «Nicht kontaktieren» (`lifecycle_status = do_not_contact`) refuses a
   **marketing** send (`contact_do_not_contact`) and never a transactional
   one: a contact who asked for an offer, an order confirmation or a
   contract gets it (Anto, 2026-11-19, KAN-217 option 2);
3. no usable row on the channel (`no_usable_contact`);
4. a **transactional** send (an offer, an order confirmation, a contract)
   needs nothing more (Anto, 2026-11-14, Q1 A: contract preparation, not
   marketing; no consent grant);
5. a **marketing** send needs D-17's three gates: the legal basis
   (`marketing_consent`, else `marketing_consent_missing`), the row's own
   grant scoped to marketing (`channel_authorises_marketing`, else
   `channel_consent_missing`) and the newsletter subscription
   (`newsletter`, else `newsletter_not_subscribed`).

**The pre-selected channel** (the dialog's opening choice; Anto's smaller
points, 2026-11-14): `email` and `whatsapp` pre-select their own channel;
`phone`, `post`, the legacy `message` and none pre-select e-mail when the
contact has a usable address, otherwise nothing. Only a pre-selection: the
verdict of the channel the user sends on still decides.

A SELECT only; it writes nothing. The contact outside the caller's group is
None (the caller answers 404, rule 7).
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contact.models.contact import (
    Contact,
    ContactEmail,
    ContactLifecycleStatus,
    ContactPhone,
    EmailType,
    Language,
    PhoneType,
    PreferredChannel,
)
from app.contact.services.contact_channels import channel_authorises_marketing
from app.contact.services.contact_common import _is_usable_row, contact_display_name
from app.contact.services.letter_salutation import letter_salutation
from app.platform.public import MessageChannel, MessagePurpose

CONTACT_MERGED = "contact_merged"
CONTACT_DO_NOT_CONTACT = "contact_do_not_contact"
NO_USABLE_CONTACT = "no_usable_contact"
MARKETING_CONSENT_MISSING = "marketing_consent_missing"
CHANNEL_CONSENT_MISSING = "channel_consent_missing"
NEWSLETTER_NOT_SUBSCRIBED = "newsletter_not_subscribed"


@dataclass(frozen=True)
class MessageRecipient:
    contact_id: uuid.UUID
    channel: MessageChannel
    purpose: MessagePurpose
    # The e-mail address or the E.164 mobile number; None without a usable row.
    address: str | None
    # The contact's correspondence language (D-01), the text's language.
    language: str
    # The letter opening, `{{customer.salutation}}` (`letter_salutation.py`),
    # in each of the four languages — the caller takes the one of the text it
    # sends, which the library may have resolved in a fallback language — and
    # the name the message is addressed to.
    salutations: Mapping[str, str]
    display_name: str

    @property
    def salutation(self) -> str:
        """The opening in the contact's own language."""

        return self.salutations[self.language]

    allowed: bool
    # The stable reason of a refusal (the module constants above); None when allowed.
    refusal: str | None


def _resolved(rows: list[Any], type_column: str, type_value: Any) -> Any | None:
    """The projection's rule (ADR-067): among the usable rows of a type, the
    one flagged primary, else the oldest."""

    of_type = [row for row in rows if getattr(row, type_column) == type_value and _is_usable_row(row)]
    return next((row for row in of_type if row.is_primary), of_type[0] if of_type else None)


def _email_row(db: Session, contact_id: uuid.UUID) -> Any | None:
    rows = list(
        db.scalars(
            select(ContactEmail).where(ContactEmail.contact_id == contact_id).order_by(ContactEmail.created_at)
        ).all()
    )
    return _resolved(rows, "email_type", EmailType.PERSONAL) or _resolved(rows, "email_type", EmailType.WORK)


def _mobile_row(db: Session, contact_id: uuid.UUID) -> Any | None:
    rows = list(
        db.scalars(
            select(ContactPhone).where(ContactPhone.contact_id == contact_id).order_by(ContactPhone.created_at)
        ).all()
    )
    return _resolved(rows, "phone_type", PhoneType.MOBILE)


def _contact_in_group(db: Session, *, group_id: uuid.UUID, contact_id: uuid.UUID) -> Contact | None:
    contact = db.get(Contact, contact_id)
    if contact is None or contact.group_id != group_id:
        return None
    return contact


def _row_for(db: Session, contact_id: uuid.UUID, channel: MessageChannel) -> Any | None:
    return _email_row(db, contact_id) if channel == MessageChannel.EMAIL else _mobile_row(db, contact_id)


def _address_of(row: Any | None, channel: MessageChannel) -> str | None:
    if row is None:
        return None
    return row.email_address if channel == MessageChannel.EMAIL else row.phone_e164


def _refusal(contact: Contact, row: Any | None, purpose: MessagePurpose) -> str | None:
    if contact.lifecycle_status == ContactLifecycleStatus.MERGED:
        return CONTACT_MERGED
    if purpose == MessagePurpose.MARKETING and contact.lifecycle_status == ContactLifecycleStatus.DO_NOT_CONTACT:
        return CONTACT_DO_NOT_CONTACT
    if row is None:
        return NO_USABLE_CONTACT
    if purpose == MessagePurpose.TRANSACTIONAL:
        return None
    if not contact.marketing_consent:
        return MARKETING_CONSENT_MISSING
    if not channel_authorises_marketing(row):
        return CHANNEL_CONSENT_MISSING
    if not contact.newsletter:
        return NEWSLETTER_NOT_SUBSCRIBED
    return None


def message_recipient(
    db: Session,
    *,
    group_id: uuid.UUID,
    contact_id: uuid.UUID,
    channel: MessageChannel,
    purpose: MessagePurpose,
) -> MessageRecipient | None:
    """Where a message on `channel` goes and whether it may go (the module
    docstring's verdict). None for a contact outside `group_id`."""

    contact = _contact_in_group(db, group_id=group_id, contact_id=contact_id)
    if contact is None:
        return None
    row = _row_for(db, contact.id, channel)
    refusal = _refusal(contact, row, purpose)
    return MessageRecipient(
        contact_id=contact.id,
        channel=channel,
        purpose=purpose,
        address=_address_of(row, channel),
        language=contact.language.value,
        salutations=MappingProxyType({language.value: letter_salutation(contact, language) for language in Language}),
        display_name=contact_display_name(contact),
        allowed=refusal is None,
        refusal=refusal,
    )


def preselected_message_channel(
    db: Session, *, group_id: uuid.UUID, contact_id: uuid.UUID
) -> tuple[bool, MessageChannel | None]:
    """(found, the channel the dialog opens on) — the module docstring's
    pre-selection. `found` is False for a contact outside `group_id`."""

    contact = _contact_in_group(db, group_id=group_id, contact_id=contact_id)
    if contact is None:
        return False, None
    if contact.preferred_channel == PreferredChannel.EMAIL:
        return True, MessageChannel.EMAIL
    if contact.preferred_channel == PreferredChannel.WHATSAPP:
        return True, MessageChannel.WHATSAPP
    return True, (MessageChannel.EMAIL if _email_row(db, contact.id) is not None else None)
