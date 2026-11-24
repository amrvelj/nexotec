"""Integration's references that a context **above** it reconciles (rule 10;
ADR-107, ADR-131; KAN-795).

Integration sits below Contacts in the layer order (ADR-131), so its own
`reconciliation.py` cannot ask `contact.public` for the contact table: a
call would point up. A column of integration's naming a higher context's
record is therefore checked from the owner's side: the owner's
`reconciliation.py` calls a builder here, passing its own target, and runs the
check in its own context's run. The call points down, and neither mapped class
leaves its context through a `public` module (ADR-162): `MessageDelivery`
stays here, the target model is a plain argument.

- `message_delivery_recipient_reference_check` —
  `integration_message_delivery.contact_id` (KAN-217 PR-5, the recipient,
  three-column) names a contact. Null on the e-mails sent before PR-5,
  which named no contact.

The delivery's `document_id` (the sending module's document) stays unchecked,
as before: the id's owner is whichever module registered the message type, so
no single context can check it (KAN-795's decision, recorded in the Target
Architecture).

A builder only: it reads nothing itself — the reconciliation run executes it.
Confined to the reconciliation modules
(`tests/architecture/test_integration_sources_only_in_reconciliation.py`).
"""

from typing import Any

from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute

from app.core.reconciliation import ReferenceCheck
from app.integration.models.mail import MessageDelivery


def message_delivery_recipient_reference_check(
    *, target_model: type[DeclarativeBase], target_id_column: InstrumentedAttribute[Any]
) -> ReferenceCheck:
    """`integration_message_delivery.contact_id` names a row of
    `target_model` (the contact table, passed by Contacts' reconciliation)."""

    return ReferenceCheck(
        label="integration_message_delivery.contact_id -> contact.id",
        source_model=MessageDelivery,
        source_row_id_column=MessageDelivery.id,
        source_fk_column=MessageDelivery.contact_id,
        target_model=target_model,
        target_id_column=target_id_column,
        nullable=True,
    )
