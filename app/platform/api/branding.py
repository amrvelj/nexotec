"""Dealer Administration › Branding (KAN-205; PRD Logo and brand assets).

Nested under /dealerships/{dealership_id} like every Business setting: the
tenant comes from the token (`require_tenant_match`: another Betrieb's id is
a 404, never a 403), any role reads (`branding` read — the logo prints on
every document anyone generates) and only the manager or platform staff
write (`branding` write). Every write takes If-Match on the dealership's
version and moves it; every write is audited.

The logo files are never public: the SPA reads them here (private,
no-store, as the user photo is served), and the renderer reads them from the
object store in-process. A signed-URL path is for a reader without a staff
session — the hosted website (D-A-26) — and comes with the bucket (KAN-402).
"""

import uuid

from fastapi import APIRouter, Body, Depends, Response
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.concurrency import check_version, require_if_match
from app.core.idempotent_route import IdempotentRoute
from app.core.object_store import ObjectStore, get_object_store
from app.core.permissions import require_read, require_write
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.organisation.models.branding_asset import BrandingAssetKind
from app.platform.organisation.schemas.branding import BrandingRead, BrandingUpdate
from app.platform.organisation.services import branding as branding_service
from app.platform.organisation.services import dealership as dealership_service
from app.platform.organisation.services.location import get_location_or_404
from app.platform.schemas.document_content import ContentDefinition
from app.platform.services.document_render import render_document

router = APIRouter(tags=["branding"], route_class=IdempotentRoute)

# A served SVG is sanitised on upload; these headers are the second wall,
# for anyone who opens the URL directly: nothing in it may run or load.
_ASSET_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
}


@router.get("/dealerships/{dealership_id}/branding", response_model=BrandingRead)
def get_branding(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("branding")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    return branding_service.branding_read(db, dealership)


@router.patch("/dealerships/{dealership_id}/branding", response_model=BrandingRead)
def update_branding(
    dealership_id: uuid.UUID,
    body: BrandingUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("branding")),
    db: Session = Depends(get_db),
):
    """Colours and placements. The contrast verdict comes back with the
    saved state; a failing pair is a warning, not a refusal."""

    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_for_update_or_404(db, dealership_id)
    check_version(dealership.version, if_match, entity_name="Dealership")
    dealership = branding_service.update_branding(db, dealership=dealership, data=body, actor_id=principal.user_id)
    return branding_service.branding_read(db, dealership)


@router.put("/dealerships/{dealership_id}/branding/assets/{kind}", response_model=BrandingRead)
def put_branding_asset(
    dealership_id: uuid.UUID,
    kind: BrandingAssetKind,
    image: bytes = Body(
        media_type="image/svg+xml",
        description="The logo file itself: SVG (preferred) or PNG at least 600 px on its long edge, at most 5 MiB. "
        "Judged by its bytes, not by the Content-Type.",
    ),
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("branding")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
):
    """One logo variant, replaced as a whole. The body is the file itself —
    never base64, never multipart; the row keeps the object-store key only.
    A plain `def`, like the photo PUT: the store write and the row lock run
    in the threadpool."""

    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_for_update_or_404(db, dealership_id)
    check_version(dealership.version, if_match, entity_name="Dealership")
    dealership = branding_service.set_asset(
        db, store, dealership=dealership, kind=kind, data=image, actor_id=principal.user_id
    )
    return branding_service.branding_read(db, dealership)


@router.get("/dealerships/{dealership_id}/branding/assets/{kind}")
def get_branding_asset(
    dealership_id: uuid.UUID,
    kind: BrandingAssetKind,
    principal: Principal = Depends(require_read("branding")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
) -> Response:
    """The file a reader of this variant gets — its own, else the primary's
    (the read's `assets[].source` says which). 404 when there is no logo."""

    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)
    stored = branding_service.read_asset(db, store, dealership_id=dealership_id, kind=kind)
    return Response(content=stored.data, media_type=stored.content_type, headers=_ASSET_HEADERS)


@router.delete("/dealerships/{dealership_id}/branding/assets/{kind}", response_model=BrandingRead)
def delete_branding_asset(
    dealership_id: uuid.UUID,
    kind: BrandingAssetKind,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("branding")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_for_update_or_404(db, dealership_id)
    check_version(dealership.version, if_match, entity_name="Dealership")
    dealership = branding_service.remove_asset(db, store, dealership=dealership, kind=kind, actor_id=principal.user_id)
    return branding_service.branding_read(db, dealership)


@router.put("/dealerships/{dealership_id}/branding/locations/{location_id}/logo", response_model=BrandingRead)
def put_location_logo(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    image: bytes = Body(
        media_type="image/svg+xml",
        description="The site's own letterhead logo: SVG (preferred) or PNG at least 600 px on its long edge, "
        "at most 5 MiB. Judged by its bytes, as the Betrieb's logo is.",
    ),
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("branding")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
):
    """KAN-206 (PRD: a per-location override of the address block and logo
    only): the logo a document printed for this site shows in place of the
    Betrieb's primary logo. Replaced as a whole; If-Match on the Betrieb's
    version, like every branding write. A site of another Betrieb is a 404."""

    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_for_update_or_404(db, dealership_id)
    check_version(dealership.version, if_match, entity_name="Dealership")
    location = get_location_or_404(db, dealership_id=dealership_id, location_id=location_id)
    dealership = branding_service.set_asset(
        db,
        store,
        dealership=dealership,
        kind=BrandingAssetKind.PRIMARY,
        data=image,
        actor_id=principal.user_id,
        location=location,
    )
    return branding_service.branding_read(db, dealership)


@router.get("/dealerships/{dealership_id}/branding/locations/{location_id}/logo")
def get_location_logo(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    principal: Principal = Depends(require_read("branding")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
) -> Response:
    """The site's own letterhead logo; 404 when it has none (its documents
    then print the Betrieb's)."""

    require_tenant_match(dealership_id, principal)
    get_location_or_404(db, dealership_id=dealership_id, location_id=location_id)
    stored = branding_service.read_location_logo(db, store, dealership_id=dealership_id, location_id=location_id)
    return Response(content=stored.data, media_type=stored.content_type, headers=_ASSET_HEADERS)


@router.delete("/dealerships/{dealership_id}/branding/locations/{location_id}/logo", response_model=BrandingRead)
def delete_location_logo(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("branding")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_for_update_or_404(db, dealership_id)
    check_version(dealership.version, if_match, entity_name="Dealership")
    location = get_location_or_404(db, dealership_id=dealership_id, location_id=location_id)
    dealership = branding_service.remove_asset(
        db, store, dealership=dealership, kind=BrandingAssetKind.PRIMARY, actor_id=principal.user_id, location=location
    )
    return branding_service.branding_read(db, dealership)


@router.get("/dealerships/{dealership_id}/branding/preview")
def get_branding_preview(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("branding")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
) -> Response:
    """The document header as it prints today: a PDF from the one renderer
    (ADR-051 — never a second, client-side one), in the Betrieb's default
    correspondence language, with the saved logo, colours and placements."""

    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    language = dealership.default_correspondence_language
    pdf = render_document(
        db,
        dealership_id=dealership_id,
        correspondence_language=language,
        content=ContentDefinition(title=branding_service.PREVIEW_TITLES[language]),
        store=store,
    )
    return Response(content=pdf, media_type="application/pdf", headers={"Cache-Control": "private, no-store"})
