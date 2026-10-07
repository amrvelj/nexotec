"""Identification endpoints (C-D / KAN-42) — `/v1/vehicle-identification`.

Both are reads from the caller's point of view: neither creates nor changes
a configuration. They are gated on **write** access to configurations
anyway, because a plate lookup and a best match are billed provider calls,
and only someone who can create a configuration from the answer has reason
to make one.

There is no route that lists anything here: a lookup always names the one
identifier it resolves (the plate-lookup cache is never enumerable).
"""

import dataclasses
import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.permissions import require_write
from app.db import get_db
from app.vehicle.schemas.identification import BestMatchProposalRead, IdentificationRead
from app.vehicle.services import identification as identification_service
from app.vehicle.services.identification import NewPriceSource

router = APIRouter(tags=["configuration"])


@router.get("/vehicle-identification", response_model=IdentificationRead)
def identify_vehicle(
    q: str = Query(min_length=1, max_length=64),
    principal: Principal = Depends(require_write("configurations")),
    db: Session = Depends(get_db),
):
    result = identification_service.identify(
        db, tenant_id=principal.tenant_id, actor_id=principal.user_id, query=q
    )
    return IdentificationRead.model_validate(dataclasses.asdict(result))


@router.get("/vehicle-identification/best-match", response_model=BestMatchProposalRead)
def propose_best_match(
    type_approval_number: str = Query(alias="typeApprovalNumber", min_length=6, max_length=6),
    new_price: Decimal = Query(alias="newPrice", gt=0),
    new_price_source: NewPriceSource = Query(alias="newPriceSource"),
    model_description: str | None = Query(default=None, alias="modelDescription", max_length=160),
    first_registration_date: dt.date | None = Query(default=None, alias="firstRegistrationDate"),
    principal: Principal = Depends(require_write("configurations")),
    db: Session = Depends(get_db),
):
    proposal = identification_service.propose_best_match(
        db,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        type_approval_number=type_approval_number,
        new_price=new_price,
        new_price_source=new_price_source,
        model_description=model_description,
        first_registration_date=first_registration_date,
    )
    return BestMatchProposalRead.model_validate(dataclasses.asdict(proposal))
