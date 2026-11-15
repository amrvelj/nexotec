"""What `customer.public` hands another context about a customer or a vehicle
party: frozen values, never the mapped row (KAN-123 step 5, ADR-162). With a
`Customer` or `VehicleParty` in hand another context could select, mutate and
flush customer's rows past the import-linter; with these it can only read.

The functions below are `public`'s doors; inside the customer context the
services keep returning rows.
"""

import dataclasses
import datetime as dt
import uuid

from sqlalchemy.orm import Session

from app.customer.models.customer import Customer, CustomerLifecycleStatus, Language
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.services import customer_common, vehicle_party, vehicle_party_reads


@dataclasses.dataclass(frozen=True)
class CustomerRef:
    """A group-scoped customer as another context reads it: who it is, how it
    is named, its correspondence language, and the states Sales refuses on."""

    id: uuid.UUID
    group_id: uuid.UUID
    customer_number: str
    first_name: str | None
    last_name: str | None
    company_name: str | None
    language: Language
    lifecycle_status: CustomerLifecycleStatus
    credit_block: bool
    credit_block_reason: str | None


def _customer_ref(row: Customer) -> CustomerRef:
    return CustomerRef(
        id=row.id,
        group_id=row.group_id,
        customer_number=row.customer_number,
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
    customer_id: uuid.UUID
    role: VehiclePartyRole
    effective_from: dt.datetime
    effective_to: dt.datetime | None


def _party_ref(row: VehicleParty) -> VehiclePartyRef:
    return VehiclePartyRef(
        id=row.id,
        vehicle_id=row.vehicle_id,
        customer_id=row.customer_id,
        role=row.role,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
    )


def get_customer_or_404(db: Session, group_id: uuid.UUID, customer_id: uuid.UUID) -> CustomerRef:
    """The customer in `group_id`, or 404 (another group's included, rule 7)."""

    return _customer_ref(customer_common.get_customer_or_404(db, group_id, customer_id))


def customer_display_name(customer: CustomerRef) -> str:
    """Company name, else first+last, else customer number (KAN-140)."""

    if customer.company_name:
        return customer.company_name
    return " ".join(part for part in [customer.first_name, customer.last_name] if part) or customer.customer_number


def allocate_vehicle_party(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    customer_id: uuid.UUID,
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
            customer_id=customer_id,
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
