"""UI translations at runtime and in Platform Administration (D-A-23,
FR-A-30…32; KAN-209).

`GET /v1/i18n/{language}` is **unauthenticated**: the sign-in, password-reset
and invitation screens render UI strings before anyone has a session, the
PRD's matrix gives the read to "everyone, at runtime", and the content is
the same for every caller — Nexotec's own UI text, which the public SPA
bundle ships anyway. Nothing tenant-shaped is in it. The SPA asks without
credentials, so the session guard never judges the request and a stale
cookie cannot 401 the sign-in page's strings.

Caching (FR-A-31, "an ETag and a long cache; a platform edit invalidates"):
the ETag is the global revision, so a browser keeps the body for as long as
it likes and revalidates on every use — `Cache-Control: no-cache` — getting a
body-less 304 while nothing was published, and the new map on the first load
after a publish. A `max-age` would serve the old text after a publish until
it ran out, which is what "invalidates" rules out. The 304 path reads one
row (the revision) and none of the strings.

Everything under `/v1/platform/ui-translations` is the Translations panel:
any role reads it (the matrix row `ui_translations`: read by everyone), only
`platform_admin` writes (403 for anyone else — the strings are no tenant's
record, so the refusal confirms nothing a 404 would hide). There is no
dealer endpoint to override a UI string (FR-A-32).

`POST /v1/i18n/missing-translations` is the browser's report of keys it
rendered as the loud marker. It takes a session (any role): an anonymous
write endpoint would let anyone fill the log; a key missing on the sign-in
screen still shows its marker, and the parity test keeps such keys from
shipping in the first place.
"""

from fastapi import APIRouter, Depends, Header, Path, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.config import get_settings
from app.core.i18n import SwissLanguage
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import require_read, require_write
from app.db import get_db
from app.platform.schemas.ui_translation import (
    KEY_PATH_PATTERN,
    MissingTranslationPage,
    MissingTranslationRead,
    MissingTranslationReport,
    MissingTranslationReportResult,
    UiTranslationKeyPage,
    UiTranslationKeyRead,
    UiTranslationMap,
    UiTranslationNamespaces,
    UiTranslationPublish,
    UiTranslationRead,
)
from app.platform.services import ui_translation as service

settings = get_settings()
router = APIRouter(tags=["ui-translations"], route_class=IdempotentRoute)

# Revalidate on every use; the ETag makes an unchanged map a 304.
_CACHE_CONTROL = "public, no-cache"
_NAMESPACE_PATTERN = r"^[A-Za-z0-9_-]+$"


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    # A list of tags, each possibly weak (a proxy may weaken ours).
    return any(tag.strip().removeprefix("W/") == etag for tag in if_none_match.split(","))


@router.get(
    "/i18n/{language}",
    response_model=UiTranslationMap,
    responses={304: {"description": "The map the caller holds (its If-None-Match) is current."}},
)
def get_ui_translation_map(
    language: SwissLanguage,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    db: Session = Depends(get_db),
):
    version = service.current_revision(db)
    etag = service.etag_for(language, version)
    headers = {"ETag": etag, "Cache-Control": _CACHE_CONTROL}
    if _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)
    body = UiTranslationMap(language=language, version=version, values=service.runtime_map(db, language))
    return JSONResponse(content=body.model_dump(mode="json", by_alias=True), headers=headers)


@router.post("/i18n/missing-translations", response_model=MissingTranslationReportResult, status_code=202)
def report_missing_translations(
    body: MissingTranslationReport,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    recorded = service.record_missing(db, language=body.language, keys=body.keys)
    db.commit()
    return MissingTranslationReportResult(recorded=recorded)


def _key_read(group: service.KeyGroup) -> UiTranslationKeyRead:
    return UiTranslationKeyRead(
        namespace=group.namespace,
        key=group.key,
        **{
            language.value: UiTranslationRead.model_validate(row, from_attributes=True)
            for language, row in group.rows.items()
        },
    )


@router.get("/platform/ui-translations", response_model=UiTranslationKeyPage)
def list_ui_translations(
    q: str | None = Query(default=None, max_length=200),
    namespace: str | None = Query(default=None, max_length=64, pattern=_NAMESPACE_PATTERN),
    edited: bool = Query(default=False, description="Only keys with a platform value in at least one language."),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(require_read("ui_translations")),
    db: Session = Depends(get_db),
):
    groups, next_cursor, total, is_estimate = service.list_keys(
        db, q=q, namespace=namespace, edited_only=edited, limit=limit, cursor=cursor
    )
    return UiTranslationKeyPage(
        items=[_key_read(g) for g in groups], next_cursor=next_cursor, total=total, total_is_estimate=is_estimate
    )


@router.get("/platform/ui-translations/namespaces", response_model=UiTranslationNamespaces)
def list_ui_translation_namespaces(
    principal: Principal = Depends(require_read("ui_translations")),
    db: Session = Depends(get_db),
):
    return UiTranslationNamespaces(items=service.list_namespaces(db))


@router.get("/platform/ui-translations/missing", response_model=MissingTranslationPage)
def list_missing_translations(
    limit: int = Query(default=settings.pagination_max_limit, ge=1, le=settings.pagination_max_limit),
    principal: Principal = Depends(require_read("ui_translations")),
    db: Session = Depends(get_db),
):
    rows = service.list_missing(db, limit=limit)
    return MissingTranslationPage(items=[MissingTranslationRead.model_validate(r, from_attributes=True) for r in rows])


@router.put("/platform/ui-translations/{namespace}/{key}/{language}", response_model=UiTranslationRead)
def publish_ui_translation(
    language: SwissLanguage,
    body: UiTranslationPublish,
    namespace: str = Path(max_length=64, pattern=_NAMESPACE_PATTERN),
    key: str = Path(max_length=200, pattern=KEY_PATH_PATTERN),
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("ui_translations")),
    db: Session = Depends(get_db),
):
    row = service.get_row_for_update_or_404(db, namespace=namespace, key=key, language=language)
    check_version(row.version, if_match, entity_name="UiTranslation")
    row = service.publish(db, row=row, value=body.value, actor_id=principal.user_id)
    db.commit()
    return UiTranslationRead.model_validate(row, from_attributes=True)


@router.post("/platform/ui-translations/{namespace}/{key}/{language}/revert", response_model=UiTranslationRead)
def revert_ui_translation(
    language: SwissLanguage,
    namespace: str = Path(max_length=64, pattern=_NAMESPACE_PATTERN),
    key: str = Path(max_length=200, pattern=KEY_PATH_PATTERN),
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("ui_translations")),
    db: Session = Depends(get_db),
):
    row = service.get_row_for_update_or_404(db, namespace=namespace, key=key, language=language)
    check_version(row.version, if_match, entity_name="UiTranslation")
    row = service.revert(db, row=row, actor_id=principal.user_id)
    db.commit()
    return UiTranslationRead.model_validate(row, from_attributes=True)
