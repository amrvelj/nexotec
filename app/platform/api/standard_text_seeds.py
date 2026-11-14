"""The standard-text seed library in Platform Administration › Translations
(D-A-23, FR-A-29; KAN-209 on KAN-208's library).

The seeds are Nexotec's starter texts (`standard_text` rows with
`dealership_id` NULL, `source = platform_seed`), one row per code × language.
The PRD's permission matrix has one row for them and the UI strings — "UI
translations and seed library": everyone reads, `platform_admin` writes — so
these routes use the `ui_translations` capability. A seed edit never touches
a Betrieb's override of it; the seed's version moving is what flags every
override "the standard text has changed, review yours" (derived on read).

Writes take If-Match on the seed row's version and run through
`update_platform_seed`, which checks the body against the placeholder
registry (422 `standard_text_placeholder_invalid`) and audits the change
(`seed_update`, `seed_revert`).
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.concurrency import require_if_match
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import require_read, require_write
from app.db import get_db
from app.platform.identity.models.standard_text_delegation import StandardTextCategory
from app.platform.models.standard_text import StandardText
from app.platform.schemas.standard_text import (
    StandardTextSeedGroup,
    StandardTextSeedList,
    StandardTextSeedRead,
    StandardTextSeedUpdate,
)
from app.platform.services import standard_text as standard_text_service

router = APIRouter(tags=["standard-text-seeds"], route_class=IdempotentRoute)


def seed_read(row: StandardText) -> StandardTextSeedRead:
    shipped = standard_text_service.shipped_seed(row.code, row.language)
    shipped_title, shipped_body = shipped if shipped is not None else (None, None)
    return StandardTextSeedRead(
        id=row.id,
        language=row.language,
        title=row.title,
        body=row.body,
        shipped_title=shipped_title,
        shipped_body=shipped_body,
        edited=shipped is not None and (row.title, row.body) != shipped,
        active=row.active,
        version=row.version,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


@router.get("/platform/standard-text-seeds", response_model=StandardTextSeedList)
def list_standard_text_seeds(
    q: str | None = Query(default=None, max_length=200, description="Matches a code, title or body, any language."),
    category: StandardTextCategory | None = None,
    principal: Principal = Depends(require_read("ui_translations")),
    db: Session = Depends(get_db),
):
    groups: dict[str, StandardTextSeedGroup] = {}
    for row in standard_text_service.search_platform_seeds(db, q=q, category=category):
        group = groups.get(row.code)
        if group is None:
            group = groups[row.code] = StandardTextSeedGroup(
                code=row.code, category=row.category, applies_to=row.applies_to, slot=row.slot
            )
        setattr(group, row.language.value, seed_read(row))
    return StandardTextSeedList(items=list(groups.values()))


@router.put("/platform/standard-text-seeds/{seed_id}", response_model=StandardTextSeedRead)
def update_standard_text_seed(
    seed_id: uuid.UUID,
    body: StandardTextSeedUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("ui_translations")),
    db: Session = Depends(get_db),
):
    seed = standard_text_service.get_platform_seed_or_404(db, seed_id, for_update=True)
    changes = body.model_dump(exclude_none=True)
    seed = standard_text_service.update_platform_seed(
        db, seed=seed, changes=changes, if_match_version=if_match, actor_id=principal.user_id
    )
    return seed_read(seed)


@router.post("/platform/standard-text-seeds/{seed_id}/revert", response_model=StandardTextSeedRead)
def revert_standard_text_seed(
    seed_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("ui_translations")),
    db: Session = Depends(get_db),
):
    seed = standard_text_service.get_platform_seed_or_404(db, seed_id, for_update=True)
    seed = standard_text_service.revert_platform_seed(
        db, seed=seed, if_match_version=if_match, actor_id=principal.user_id
    )
    return seed_read(seed)
