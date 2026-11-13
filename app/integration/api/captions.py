"""KAN-588 — the provider caption vocabulary on the catalogue administration:
when each language was last fetched, and «Captions aktualisieren». Platform
staff only (`require_access_role()`); the refresh runs in the Betrieb the
staff user acts in (tenant from the token, rule 7), under their own token."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal, require_access_role
from app.core.errors import ConflictError
from app.core.idempotent_route import IdempotentRoute
from app.db import get_db
from app.integration.schemas.caption import CaptionFetchList, CaptionFetchRead
from app.integration.services import captions as captions_service
from app.integration.services.caption_refresh import refresh_captions

router = APIRouter(tags=["integrations"], route_class=IdempotentRoute)


def _list(fetches: list[captions_service.CaptionFetch]) -> CaptionFetchList:
    return CaptionFetchList(
        items=[
            CaptionFetchRead(provider=f.provider, language=f.lang, fetched_at=f.fetched_at, caption_count=f.caption_count)
            for f in fetches
        ]
    )


@router.get("/integrations/captions/fetches", response_model=CaptionFetchList)
def list_caption_fetches(
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    """Per provider and language: when its captions were last fetched (by
    «Test connection» or a refresh) and how many codes came back."""

    return _list(captions_service.list_caption_fetches(db))


@router.post("/integrations/captions/refresh", response_model=CaptionFetchList)
def refresh_provider_captions(
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    """«Captions aktualisieren»: the four languages fetched again through the
    acting Betrieb's vehicle-data connection, under the staff user's own
    token. 409 `no_provider_connection`; 422 `provider_unavailable`."""

    if principal.tenant_id is None or principal.user_id is None:
        raise ConflictError("Act in a Betrieb to refresh its provider's captions.", details={"reason": "no_provider_connection"})
    return _list(refresh_captions(db, tenant_id=principal.tenant_id, actor_id=principal.user_id))
