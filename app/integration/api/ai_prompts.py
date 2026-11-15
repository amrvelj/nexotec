"""The AI prompt library (KAN-227, D-A-33, FR-A-38) — the dealer side at
`/administration/ai` and the seed side in Platform Administration ›
Translations.

Dealer routes nest under `/dealerships/{dealership_id}` like the standard
texts: the tenant from the token (`require_tenant_match`, 404-not-403 on
another Betrieb's path id). The matrix row `ai_prompts`: inventory and sales
read (they use the prompt through «Text vorschlagen»), the manager writes.
Seeds are written only under `ui_translations` (the one matrix row "UI
translations and seed library": `platform_admin`), as the standard-text seeds
are; a seed is never written through the dealer routes.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.concurrency import require_if_match
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import require_read, require_write
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.integration.models.ai_prompt import IntegrationAiPrompt
from app.integration.schemas.ai_prompt import (
    AiConnectionInUseRead,
    AiPromptCodeRead,
    AiPromptCreate,
    AiPromptList,
    AiPromptRead,
    AiPromptSeedGroup,
    AiPromptSeedList,
    AiPromptSeedRead,
    AiPromptSeedUpdate,
    AiPromptUpdate,
)
from app.integration.services import ai_prompts as prompt_service
from app.integration.services.ai_generation import ai_connection_in_use
from app.integration.services.ai_prompt_registry import REGISTRY, prompt_code
from app.integration.services.ai_prompt_seed import shipped_prompt

router = APIRouter(tags=["ai-prompts"], route_class=IdempotentRoute)


def _read(row: IntegrationAiPrompt, versions: dict[uuid.UUID, int]) -> AiPromptRead:
    return AiPromptRead(
        id=row.id,
        code=row.code,
        language=row.language,
        title=row.title,
        system_prompt=row.system_prompt,
        user_prompt=row.user_prompt,
        source=row.source,
        overridden_from_id=row.overridden_from_id,
        seed_changed=prompt_service.seed_changed(row, versions),
        active=row.active,
        version=row.version,
        updated_at=row.updated_at,
    )


def _one(db: Session, row: IntegrationAiPrompt) -> AiPromptRead:
    return _read(row, prompt_service.seed_versions(db, [row]))


@router.get("/dealerships/{dealership_id}/ai-prompts", response_model=AiPromptList)
def list_ai_prompts(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("ai_prompts")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    rows = prompt_service.list_prompts(db, tenant_id=dealership_id)
    versions = prompt_service.seed_versions(db, rows)
    in_use = ai_connection_in_use(db, tenant_id=dealership_id)
    return AiPromptList(
        items=[_read(row, versions) for row in rows],
        codes=[AiPromptCodeRead(code=entry.code, placeholders=list(entry.placeholders)) for entry in REGISTRY],
        connection=None
        if in_use is None
        else AiConnectionInUseRead(
            connection_id=in_use.connection_id,
            display_name=in_use.display_name,
            provider_code=in_use.provider_code,
            provider_name=in_use.provider_name,
            status=in_use.status,
        ),
    )


@router.post("/dealerships/{dealership_id}/ai-prompts", response_model=AiPromptRead, status_code=201)
def create_ai_prompt(
    dealership_id: uuid.UUID,
    body: AiPromptCreate,
    principal: Principal = Depends(require_write("ai_prompts")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    row = prompt_service.create_override(
        db,
        tenant_id=dealership_id,
        code=body.code,
        language=body.language,
        system_prompt=body.system_prompt,
        user_prompt=body.user_prompt,
        actor_id=principal.user_id,
    )
    return _one(db, row)


@router.patch("/dealerships/{dealership_id}/ai-prompts/{prompt_id}", response_model=AiPromptRead)
def update_ai_prompt(
    dealership_id: uuid.UUID,
    prompt_id: uuid.UUID,
    body: AiPromptUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("ai_prompts")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    row = prompt_service.get_dealer_prompt_or_404(db, tenant_id=dealership_id, prompt_id=prompt_id, for_update=True)
    row = prompt_service.update_override(
        db, row=row, changes=body.model_dump(exclude_unset=True), if_match_version=if_match, actor_id=principal.user_id
    )
    return _one(db, row)


@router.delete("/dealerships/{dealership_id}/ai-prompts/{prompt_id}", status_code=204)
def delete_ai_prompt(
    dealership_id: uuid.UUID,
    prompt_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("ai_prompts")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    row = prompt_service.get_dealer_prompt_or_404(db, tenant_id=dealership_id, prompt_id=prompt_id, for_update=True)
    prompt_service.delete_override(db, row=row, if_match_version=if_match, actor_id=principal.user_id)
    return Response(status_code=204)


# --- seeds: Platform Administration › Translations --------------------------------


def _seed_read(row: IntegrationAiPrompt) -> AiPromptSeedRead:
    shipped = shipped_prompt(row.code, row.language)
    title, system_prompt, user_prompt = shipped if shipped is not None else (None, None, None)
    return AiPromptSeedRead(
        id=row.id,
        language=row.language,
        title=row.title,
        system_prompt=row.system_prompt,
        user_prompt=row.user_prompt,
        shipped_title=title,
        shipped_system_prompt=system_prompt,
        shipped_user_prompt=user_prompt,
        edited=shipped is not None and (row.title, row.system_prompt, row.user_prompt) != shipped,
        version=row.version,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


@router.get("/platform/ai-prompt-seeds", response_model=AiPromptSeedList)
def list_ai_prompt_seeds(
    q: str | None = Query(default=None, max_length=200, description="Matches a code, title or prompt, any language."),
    principal: Principal = Depends(require_read("ui_translations")),
    db: Session = Depends(get_db),
):
    groups: dict[str, AiPromptSeedGroup] = {}
    for row in prompt_service.list_seeds(db, q=q):
        group = groups.get(row.code)
        if group is None:
            entry = prompt_code(row.code)
            group = groups[row.code] = AiPromptSeedGroup(
                code=row.code, placeholders=list(entry.placeholders) if entry else []
            )
        setattr(group, row.language.value, _seed_read(row))
    return AiPromptSeedList(items=list(groups.values()))


@router.put("/platform/ai-prompt-seeds/{seed_id}", response_model=AiPromptSeedRead)
def update_ai_prompt_seed(
    seed_id: uuid.UUID,
    body: AiPromptSeedUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("ui_translations")),
    db: Session = Depends(get_db),
):
    seed = prompt_service.get_seed_or_404(db, seed_id, for_update=True)
    seed = prompt_service.update_seed(
        db, seed=seed, changes=body.model_dump(exclude_none=True), if_match_version=if_match, actor_id=principal.user_id
    )
    return _seed_read(seed)


@router.post("/platform/ai-prompt-seeds/{seed_id}/revert", response_model=AiPromptSeedRead)
def revert_ai_prompt_seed(
    seed_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("ui_translations")),
    db: Session = Depends(get_db),
):
    seed = prompt_service.get_seed_or_404(db, seed_id, for_update=True)
    seed = prompt_service.revert_seed(db, seed=seed, if_match_version=if_match, actor_id=principal.user_id)
    return _seed_read(seed)
