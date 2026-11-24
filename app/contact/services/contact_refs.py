"""What `contact.public` hands another context about a contact or a vehicle
party: frozen values, never the mapped row (KAN-123 step 5, ADR-162). With a
`Contact` or `VehicleParty` in hand another context could select, mutate and
flush contact's rows past the import-linter; with these it can only read.

The functions below are `public`'s doors; inside the contact context the
services keep returning rows.
"""

import dataclasses
import datetime as dt
import uuid

from sqlalchemy.orm import Session

from app.contact.models.contact import Contact, ContactLifecycleStatus, Language
from app.contact.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.contact.services import contact_common, vehicle_party, vehicle_party_reads


@dataclasses.dataclass(frozen=True)
class ContactRef:
    """A group-scoped contact as another context reads it: who it is, how it
    is named, its correspondence language, and the states Sales refuses on."""

    id: uuid.UUID
    group_id: uuid.UUID
    contact_number: str
    first_name: str | None
    last_name: str | None
    company_name: str | None
    language: Language
    lifecycle_status: ContactLifecycleStatus
    credit_block: bool
    credit_block_reason: str | None


def _contact_ref(row: Contact) -> ContactRef:
    return ContactRef(
        id=row.id,
        group_id=row.group_id,
        contact_number=row.contact_number,
        first_name=row.first_name,
        last_name=row.last_name,
        company_name=row.company_name,
        language=row.language,
        lifecycle_status=row.lifecycle_status,
        credit_block=row.credit_block,
        credit_block_reason=row.credit_block_reason,
    )


@dataclasses.dataclass(frozen=True)
class VehiclePartyRef:
    """One owner/keeper/driver allocation of a vehicle-mdm record."""

    id: uuid.UUID
    vehicle_id: uuid.UUID
    contact_id: uuid.UUID
    role: VehiclePartyRole
    effective_from: dt.datetime
    effective_to: dt.datetime | None


def _party_ref(row: VehicleParty) -> VehiclePartyRef:
    return VehiclePartyRef(
        id=row.id,
        vehicle_id=row.vehicle_id,
        contact_id=row.contact_id,
        role=row.role,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
    )


def get_contact_or_404(db: Session, group_id: uuid.UUID, contact_id: uuid.UUID) -> ContactRef:
    """The contact in `group_id`, or 404 (another group's included, rule 7)."""

    return _contact_ref(contact_common.get_contact_or_404(db, group_id, contact_id))


def contact_display_name(contact: ContactRef) -> str:
    """Company name, else first+last, else contact number (KAN-140)."""

    if contact.company_name:
        return contact.company_name
    return " ".join(part for part in [contact.first_name, contact.last_name] if part) or contact.contact_number


def allocate_vehicle_party(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    contact_id: uuid.UUID,
    role: VehiclePartyRole,
    group_id: uuid.UUID,
    actor_id: uuid.UUID,
    acting_dealership_id: uuid.UUID | None,
) -> VehiclePartyRef:
    """`vehicle_party.allocate_vehicle_party`, answered as a value.
    `acting_dealership_id`: the Betrieb the allocation is made in (KAN-722)."""

    return _party_ref(
        vehicle_party.allocate_vehicle_party(
            db,
            vehicle_id=vehicle_id,
            contact_id=contact_id,
            role=role,
            group_id=group_id,
            actor_id=actor_id,
            acting_dealership_id=acting_dealership_id,
        )
    )


def list_vehicle_party_holders(
    db: Session, *, vehicle_id: uuid.UUID, group_id: uuid.UUID, include_closed: bool = False
) -> list[tuple[VehiclePartyRef, str]]:
    """`vehicle_party_reads.list_vehicle_party_holders`, answered as values."""

    return [
        (_party_ref(party), name)
        for party, name in vehicle_party_reads.list_vehicle_party_holders(
            db, vehicle_id=vehicle_id, group_id=group_id, include_closed=include_closed
        )
    ]
