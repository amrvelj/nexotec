"""Creating a fixed labour rate agreed with a contact (KAN-806; D-A-19),
composed at the composition root (the KAN-629 pattern, ADR-131).

The agreement is platform's data, but its contact is contact's, and contact
sits above platform: platform never imports it. So this route, which may
import both, resolves the contact in the Betrieb's group — another group's
contact is 404 (rule 7), a merged one 409 (pick the one it was merged into)
— and hands its id and label to platform, which checks the rest and writes.
It only reads contact; the write is platform's alone, one transaction.

The route is part of Dealer Administration: `session_guard` lets a
Betrieb still being set up reach it (its prefix is listed there).
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.contact import public as contact
from app.core.auth import Principal
from app.core.errors import ConflictError
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import require_write
from app.db import get_db
from app.platform import public as platform

router = APIRouter(tags=["labour-rates"], route_class=IdempotentRoute)


@router.post(
    "/dealerships/{dealership_id}/labour-rate-agreements",
    response_model=platform.LabourRateAgreementRead,
    status_code=201,
)
def create_labour_rate_agreement(
    dealership_id: uuid.UUID,
    body: platform.LabourRateAgreementCreate,
    principal: Principal = Depends(require_write("labour_rates")),
    db: Session = Depends(get_db),
):
    """A fixed rate agreed with a contact for a category, from a date today or
    later (ruling 178); with `kind: end`, the end of the one before that date.
    Typed as the business's O16 says (Anto, 2026-11-25)."""

    platform.labour_rates_guard(db, dealership_id, principal)
    # KAN-831: before the contact is read — a merge consumer moving this contact's
    # agreements waits for this create to commit (and moves its row), or this
    # create waits and reads the contact merged.
    platform.lock_agreement_contact(db, body.contact_id)
    party = contact.get_contact_or_404(db, principal.group_id, body.contact_id)
    if party.lifecycle_status == contact.ContactLifecycleStatus.MERGED:
        raise ConflictError(
            "This contact was merged into another one; choose the contact that remains.",
            details={"reason": "labour_rate_agreement_contact_merged"},
        )
    return platform.create_labour_rate_agreement(
        db,
        dealership_id=dealership_id,
        principal=principal,
        contact_label=contact.contact_display_name(party),
        body=body,
    )
