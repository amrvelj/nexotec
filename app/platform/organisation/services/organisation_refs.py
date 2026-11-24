"""What `platform.public` hands another context about a dealer group, a
Betrieb or a location: frozen values, never the mapped row (KAN-123 step 8,
ADR-162). With a `DealerGroup`, `Dealership` or `Location` in hand another
context could select, mutate and flush platform's rows past the
import-linter; with these it can only read.

The functions below are `public`'s doors; inside platform the services keep
returning rows.
"""

import dataclasses
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.platform.organisation.schemas.dealership import DealerGroupRead
from app.platform.organisation.services import dealership as dealership_service
from app.platform.organisation.services import location as location_service


@dataclasses.dataclass(frozen=True)
class DealershipSummary:
    """A Betrieb as another context reads it: its id, its dealer group (a
    contact is group-scoped, ADR-014) and the VAT rate its documents and the
    notional input tax print with (None until the Betrieb sets it)."""

    id: uuid.UUID
    dealer_group_id: uuid.UUID
    vat_rate: Decimal | None


@dataclasses.dataclass(frozen=True)
class LocationSummary:
    """One of a Betrieb's locations: its id, its Betrieb and its name (the
    label another context copies beside the id, rule 2)."""

    id: uuid.UUID
    dealership_id: uuid.UUID
    name: str


def get_dealership_or_404(db: Session, dealership_id: uuid.UUID) -> DealershipSummary:
    """The Betrieb by id, a 404 when there is none."""

    row = dealership_service.get_dealership_or_404(db, dealership_id)
    return DealershipSummary(id=row.id, dealer_group_id=row.dealer_group_id, vat_rate=row.vat_rate)


def get_location_or_404(db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID) -> LocationSummary:
    """By (dealership, id): another Betrieb's location is a 404 (rule 7)."""

    row = location_service.get_location_or_404(db, dealership_id=dealership_id, location_id=location_id)
    return LocationSummary(id=row.id, dealership_id=row.tenant_id, name=row.name)


def get_dealer_group_or_404(db: Session, dealer_group_id: uuid.UUID) -> DealerGroupRead:
    """The dealer group by id as its API read model, a 404 when there is none."""

    row = dealership_service.get_dealer_group_or_404(db, dealer_group_id)
    return DealerGroupRead.model_validate(row, from_attributes=True)


def enable_group_read(
    db: Session, *, dealer_group_id: uuid.UUID, actor_id: uuid.UUID, legal_basis_recorded: bool
) -> DealerGroupRead:
    """ADR-030 (2): flip the group's read on — platform's write, on the row it
    loads itself. The caller passes whether a legal basis was ever recorded
    (contact's fact, KAN-629); the refusals are the service's."""

    row = dealership_service.get_dealer_group_or_404(db, dealer_group_id)
    enabled = dealership_service.enable_group_read(
        db, dealer_group=row, actor_id=actor_id, legal_basis_recorded=legal_basis_recorded
    )
    return DealerGroupRead.model_validate(enabled, from_attributes=True)
