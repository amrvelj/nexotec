"""Standard texts — the Textbaustein library (KAN-208; Dealer
Administration PRD "Standard texts").

Nested under /dealerships/{dealership_id} like the document template:
tenant from the token (`require_tenant_match`, 404-not-403 on another
Betrieb's path id). Any role reads (`require_read("standard_texts")`). Every
write carries `{category}` in its path behind
the delegation-aware gate (`require_write_or_delegated` on the
standard-texts capability) — the manager, platform
staff, or a user holding a delegation for that one category (FR-A-28,
D-A-23) — and the service binds what it writes to that category, so a
delegation never reaches another category's text. Seeds are not written
here: a dealer edit of one creates the Betrieb's override (FR-A-29 keeps the
seed itself to platform_admin).
"""

import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import require_if_match
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import require_read, require_write_or_delegated
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.models.standard_text import StandardText
from app.platform.models.standard_text_delegation import StandardTextCategory
from app.platform.schemas.standard_text import (
    StandardTextCreate,
    StandardTextList,
    StandardTextPreviewRead,
    StandardTextPreviewRequest,
    StandardTextRead,
    StandardTextRegistryRead,
    StandardTextTargetRead,
    StandardTextUpdate,
)
from app.platform.services import standard_text as standard_text_service
from app.platform.services.standard_text_placeholders import TARGETS, Placeholder

router = APIRouter(tags=["standard-texts"], route_class=IdempotentRoute)


def standard_text_read(row: StandardText, versions: dict[uuid.UUID, int]) -> StandardTextRead:
    return StandardTextRead(
        id=row.id,
        dealership_id=row.dealership_id,
        location_id=row.location_id,
        scope=row.scope,
        code=row.code,
        category=row.category,
        applies_to=row.applies_to,
        slot=row.slot,
        language=row.language,
        title=row.title,
        body=row.body,
        source=row.source,
        overridden_from_id=row.overridden_from_id,
        seed_changed=standard_text_service.seed_changed(row, versions),
        is_default=row.is_default,
        active=row.active,
        sort_order=row.sort_order,
        template_approved=row.template_approved,
        provider_template_id=row.provider_template_id,
        version=row.version,
        updated_at=row.updated_at,
    )


def _one_read(db: Session, row: StandardText) -> StandardTextRead:
    return standard_text_read(row, standard_text_service.seed_versions(db, [row]))


@router.get("/standard-text-registry", response_model=StandardTextRegistryRead)
def get_standard_text_registry(_principal: Principal = Depends(get_current_principal)):
    """The closed placeholder set and, per (category, appliesTo), its slots
    and the placeholders valid there (FR-A-07). Static; the same for every
    Betrieb."""

    return StandardTextRegistryRead(
        placeholders=[token.value for token in Placeholder],
        targets=[
            StandardTextTargetRead(
                category=target.category,
                applies_to=target.applies_to,
                slots=list(target.slots),
                placeholders=[token.value for token in Placeholder if token in target.placeholders],
            )
            for target in TARGETS
        ],
    )


@router.get("/dealerships/{dealership_id}/standard-texts", response_model=StandardTextList)
def list_standard_texts(
    dealership_id: uuid.UUID,
    category: StandardTextCategory | None = Query(default=None),
    principal: Principal = Depends(require_read("standard_texts")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    rows = standard_text_service.list_texts(db, dealership_id=dealership_id, category=category)
    versions = standard_text_service.seed_versions(db, rows)
    return StandardTextList(
        items=[standard_text_read(row, versions) for row in rows],
        writable_categories=standard_text_service.writable_categories(db, principal, dealership_id),
    )


@router.post("/dealerships/{dealership_id}/standard-text-previews", response_model=StandardTextPreviewRead)
def preview_standard_text(
    dealership_id: uuid.UUID,
    body: StandardTextPreviewRequest,
    principal: Principal = Depends(require_read("standard_texts")),
    db: Session = Depends(get_db),
):
    """The editor's preview with sample data, through the renderer a
    document uses — an unknown or invalid token is the same 422 a real
    render gives (`standard_text_placeholder_unresolved`). Writes nothing."""

    require_tenant_match(dealership_id, principal)
    text = standard_text_service.preview(
        db,
        dealership_id=dealership_id,
        category=body.category,
        applies_to=body.applies_to,
        language=body.language,
        body=body.body,
        location_id=body.location_id,
    )
    return StandardTextPreviewRead(text=text)


@router.post("/dealerships/{dealership_id}/standard-texts/{category}", response_model=StandardTextRead, status_code=201)
def create_standard_text(
    dealership_id: uuid.UUID,
    category: StandardTextCategory,
    body: StandardTextCreate,
    principal: Principal = Depends(require_write_or_delegated("standard_texts")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    row = standard_text_service.create_dealer_text(
        db,
        dealership_id=dealership_id,
        category=category,
        data=standard_text_service.DealerTextInput(
            code=body.code,
            language=body.language,
            title=body.title,
            body=body.body,
            applies_to=body.applies_to,
            slot=body.slot,
            location_id=body.location_id,
            is_default=body.is_default,
            active=body.active,
            sort_order=body.sort_order,
            template_approved=body.template_approved,
            provider_template_id=body.provider_template_id,
        ),
        actor_id=principal.user_id,
    )
    return _one_read(db, row)


@router.patch("/dealerships/{dealership_id}/standard-texts/{category}/{text_id}", response_model=StandardTextRead)
def update_standard_text(
    dealership_id: uuid.UUID,
    category: StandardTextCategory,
    text_id: uuid.UUID,
    body: StandardTextUpdate,
    principal: Principal = Depends(require_write_or_delegated("standard_texts")),
    if_match: int = Depends(require_if_match),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    row = standard_text_service.get_dealer_text_or_404(
        db, dealership_id=dealership_id, category=category, text_id=text_id, for_update=True
    )
    row = standard_text_service.update_dealer_text(
        db,
        row=row,
        changes=body.model_dump(exclude_unset=True),
        if_match_version=if_match,
        actor_id=principal.user_id,
    )
    return _one_read(db, row)


@router.delete("/dealerships/{dealership_id}/standard-texts/{category}/{text_id}", status_code=204)
def delete_standard_text(
    dealership_id: uuid.UUID,
    category: StandardTextCategory,
    text_id: uuid.UUID,
    principal: Principal = Depends(require_write_or_delegated("standard_texts")),
    if_match: int = Depends(require_if_match),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    row = standard_text_service.get_dealer_text_or_404(
        db, dealership_id=dealership_id, category=category, text_id=text_id, for_update=True
    )
    standard_text_service.delete_dealer_text(db, row=row, if_match_version=if_match, actor_id=principal.user_id)
    return Response(status_code=204)
