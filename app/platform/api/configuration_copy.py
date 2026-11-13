"""Copy configuration from a sister Betrieb (KAN-220; Dealer Administration
PRD D-A-01, FR-A-01). The service holds the rules; see its docstring.

Both routes answer 404 unless the caller holds the manager flag in the
target — the active dealership — and, for the copy, in the source too
(platform staff hold no flag and get 404 too): a caller learns nothing about a Betrieb it does not
manage (rule 7). Not `require_write`, whose refusal is a 403.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.idempotent_route import IdempotentRoute
from app.db import get_db
from app.platform.schemas.configuration_copy import (
    ConfigurationCopyCounts,
    ConfigurationCopyItem,
    ConfigurationCopyRequest,
    ConfigurationCopyResult,
    ConfigurationCopySource,
    ConfigurationCopySourceList,
)
from app.platform.services import configuration_copy as copy_service

router = APIRouter(tags=["dealerships"], route_class=IdempotentRoute)


@router.get("/dealerships/{dealership_id}/copy-configuration/sources", response_model=ConfigurationCopySourceList)
def list_copy_sources(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    target = copy_service.require_target(db, principal, dealership_id)
    return ConfigurationCopySourceList(
        items=[
            ConfigurationCopySource(id=option.id, legal_name=option.legal_name)
            for option in copy_service.list_sources(db, principal, target=target)
        ]
    )


@router.post("/dealerships/{dealership_id}/copy-configuration", response_model=ConfigurationCopyResult)
def copy_configuration(
    dealership_id: uuid.UUID,
    body: ConfigurationCopyRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    report = copy_service.copy_configuration(
        db,
        principal,
        target_id=dealership_id,
        source_id=body.source_dealership_id,
        subsets=set(body.subsets),
        dry_run=body.dry_run,
    )
    return ConfigurationCopyResult(
        source_dealership_id=report.source_dealership_id,
        source_legal_name=report.source_legal_name,
        dry_run=report.dry_run,
        items=[
            ConfigurationCopyItem(
                subset=item.subset,
                key=item.key,
                outcome=item.outcome,
                label=item.label,
                language=item.language,
                reason=item.reason,
            )
            for item in report.items
        ],
        counts=ConfigurationCopyCounts(**report.counts()),
    )
