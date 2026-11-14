"""Dealer Administration › Publishing (KAN-225; PRD Publishing profile,
FR-A-04, FR-A-05, FR-A-27, A-RULE-2).

Nested under /dealerships/{dealership_id} like every Business setting: the
tenant comes from the token (`require_tenant_match`: another Betrieb's id is
a 404). The router carries the `sales` entitlement gate (a Betrieb without
Sales publishes nothing — 404 for everyone, staff included), any role reads
(`publishing_profile` read), the matrix's writers write (inventory and the
manager, `publishing_profile` write).

Every write takes If-Match on the profile's version — one version for the
whole screen — and answers the whole profile with the new version.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Body, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.concurrency import check_version, require_if_match
from app.core.idempotent_route import IdempotentRoute
from app.core.object_store import ObjectStore, get_object_store
from app.core.permissions import ModuleCode, require_entitlement, require_read, require_write
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.organisation.models.branding_asset import PublishingImageKind
from app.platform.organisation.models.dealership import Dealership
from app.platform.organisation.models.publishing_profile import (
    OVERRIDABLE_LOCALISED_FIELDS,
    OVERRIDABLE_SCALAR_FIELDS,
    PublishingProfile,
)
from app.platform.organisation.schemas.publishing_profile import (
    AgbDocumentRead,
    EffectiveProfileRead,
    ImageOrderUpdate,
    LeadRoutingRead,
    LeadRoutingUpdate,
    LocationOverrideRead,
    LocationOverrideUpdate,
    MissingItemRead,
    ProfileValidationRead,
    PublishingImageRead,
    PublishingProfileRead,
    PublishingProfileUpdate,
)
from app.platform.organisation.services import dealership as dealership_service
from app.platform.organisation.services import publishing_profile as service
from app.platform.organisation.services.location import get_location_for_update_or_404, get_location_or_404

router = APIRouter(
    tags=["publishing-profile"],
    route_class=IdempotentRoute,
    dependencies=[Depends(require_entitlement(ModuleCode.SALES))],
)

_FILE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}
_PREFIX = "/dealerships/{dealership_id}/publishing-profile"


def _override_read(row: PublishingProfile) -> LocationOverrideRead:
    assert row.location_id is not None
    return LocationOverrideRead(
        location_id=row.location_id,
        **{f: dict(getattr(row, f) or {}) for f in OVERRIDABLE_LOCALISED_FIELDS},
        **{f: getattr(row, f) for f in OVERRIDABLE_SCALAR_FIELDS},
    )


def profile_read(db: Session, dealership: Dealership) -> PublishingProfileRead:
    profile = service.read_profile(db, dealership.id)
    return PublishingProfileRead(
        dealership_id=dealership.id,
        trade_name=dealership.trade_name,
        website=dealership.website,
        website_domain=profile.website_domain,
        is_published=profile.is_published,
        social_profiles=dict(profile.social_profiles or {}),
        impressum=dict(profile.impressum or {}),
        privacy_notice=dict(profile.privacy_notice or {}),
        withdrawal_wording=dict(profile.withdrawal_wording or {}),
        agb_url=profile.agb_url,
        agb_document=(
            AgbDocumentRead(byte_size=profile.agb_byte_size or 0, uploaded_at=profile.agb_uploaded_at)
            if profile.agb_object_key is not None and profile.agb_uploaded_at is not None
            else None
        ),
        location_overrides=[_override_read(row) for row in service.list_overrides(db, dealership.id)],
        lead_routing=[
            LeadRoutingRead(
                location_id=location.id,
                location_name=location.name,
                lead_email=location.lead_email,
                lead_phone=routing.lead_phone if routing else None,
                creates_task=routing.creates_task if routing else False,
                recipient_user_id=routing.recipient_user_id if routing else None,
                recipient_role=routing.recipient_role if routing else None,  # type: ignore[arg-type]
            )
            for location, routing in service.list_lead_routing(db, dealership.id)
        ],
        images=[
            PublishingImageRead(
                id=image.id,
                location_id=image.location_id,  # type: ignore[arg-type]
                kind=PublishingImageKind(image.kind.value),
                position=image.position or 0,
                is_primary=image.is_primary,
                mime_type=image.mime_type,
                width=image.width,
                height=image.height,
                byte_size=image.byte_size,
                uploaded_at=image.uploaded_at,
            )
            for image in service.list_images(db, dealership.id)
        ],
        version=profile.version,
        updated_at=profile.updated_at,
        **{f: dict(getattr(profile, f) or {}) for f in OVERRIDABLE_LOCALISED_FIELDS},
        **{f: getattr(profile, f) for f in OVERRIDABLE_SCALAR_FIELDS},
    )


def _locked_profile(db: Session, dealership_id: uuid.UUID, if_match: int) -> PublishingProfile:
    profile = service.profile_for_update(db, dealership_id)
    check_version(profile.version, if_match, entity_name="PublishingProfile")
    return profile


@router.get(_PREFIX, response_model=PublishingProfileRead)
def get_publishing_profile(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("publishing_profile")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    return profile_read(db, dealership)  # a read never writes


@router.patch(_PREFIX, response_model=PublishingProfileRead)
def update_publishing_profile(
    dealership_id: uuid.UUID,
    body: PublishingProfileUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("publishing_profile")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    profile = _locked_profile(db, dealership_id, if_match)
    service.update_profile(db, profile=profile, data=body, actor_id=principal.user_id)
    return profile_read(db, dealership)


# --- Location overrides -----------------------------------------------------------------------


@router.post(f"{_PREFIX}/locations/{{location_id}}", response_model=PublishingProfileRead, status_code=201)
def create_location_override(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    body: LocationOverrideUpdate | None = None,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("publishing_profile")),
    db: Session = Depends(get_db),
):
    """Switches the site's override on (the screen's toggle). Idempotency-Key
    is honoured; switching on an override that exists answers it unchanged."""

    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    location = get_location_or_404(db, dealership_id=dealership_id, location_id=location_id)
    profile = _locked_profile(db, dealership_id, if_match)
    service.create_override(
        db, profile=profile, location=location, data=body or LocationOverrideUpdate(), actor_id=principal.user_id
    )
    return profile_read(db, dealership)


@router.patch(f"{_PREFIX}/locations/{{location_id}}", response_model=PublishingProfileRead)
def update_location_override(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    body: LocationOverrideUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("publishing_profile")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    location = get_location_or_404(db, dealership_id=dealership_id, location_id=location_id)
    profile = _locked_profile(db, dealership_id, if_match)
    service.update_override(db, profile=profile, location=location, data=body, actor_id=principal.user_id)
    return profile_read(db, dealership)


@router.delete(f"{_PREFIX}/locations/{{location_id}}", response_model=PublishingProfileRead)
def delete_location_override(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("publishing_profile")),
    db: Session = Depends(get_db),
):
    """Switches the site's override off: it inherits everything again."""

    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    location = get_location_or_404(db, dealership_id=dealership_id, location_id=location_id)
    profile = _locked_profile(db, dealership_id, if_match)
    service.delete_override(db, profile=profile, location=location, actor_id=principal.user_id)
    return profile_read(db, dealership)


@router.get(f"{_PREFIX}/effective", response_model=EffectiveProfileRead)
def get_effective_profile(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID | None = Query(default=None, alias="locationId"),
    principal: Principal = Depends(require_read("publishing_profile")),
    db: Session = Depends(get_db),
):
    """What a channel publishes for the site: its override over the Betrieb."""

    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    if location_id is not None:
        get_location_or_404(db, dealership_id=dealership_id, location_id=location_id)
    effective = service.effective_profile(db, dealership, location_id)
    return EffectiveProfileRead(
        dealership_id=effective.dealership_id,
        location_id=effective.location_id,
        trade_name=effective.trade_name,
        has_agb_document=effective.has_agb_document,
        **effective.values,
    )


@router.get(f"{_PREFIX}/validation", response_model=ProfileValidationRead)
def validate_publishing_profile(
    dealership_id: uuid.UUID,
    channel: Literal["website"] = Query(default="website"),
    principal: Principal = Depends(require_read("publishing_profile")),
    db: Session = Depends(get_db),
):
    """FR-A-05: what the channel needs that the profile lacks, as stable
    codes the SPA renders in the dealer's words."""

    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    missing = service.missing_items(db, dealership, channel)
    return ProfileValidationRead(
        channel=channel,
        ready=not missing,
        missing=[
            MissingItemRead(code=m.code, section=m.section, location_id=m.location_id, location_name=m.location_name)  # type: ignore[arg-type]
            for m in missing
        ],
    )


# --- AGB file -------------------------------------------------------------------------------


@router.put(f"{_PREFIX}/agb", response_model=PublishingProfileRead)
def put_agb_document(
    dealership_id: uuid.UUID,
    document: bytes = Body(media_type="application/pdf", description="The AGB as a PDF, at most 10 MiB."),
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("publishing_profile")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    profile = _locked_profile(db, dealership_id, if_match)
    service.set_agb(db, store, profile=profile, data=document, actor_id=principal.user_id)
    return profile_read(db, dealership)


@router.get(f"{_PREFIX}/agb")
def get_agb_document(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("publishing_profile")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
) -> Response:
    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)
    stored = service.read_agb(db, store, dealership_id=dealership_id)
    return Response(content=stored.data, media_type="application/pdf", headers=_FILE_HEADERS)


@router.delete(f"{_PREFIX}/agb", response_model=PublishingProfileRead)
def delete_agb_document(
    dealership_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("publishing_profile")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    profile = _locked_profile(db, dealership_id, if_match)
    service.remove_agb(db, store, profile=profile, actor_id=principal.user_id)
    return profile_read(db, dealership)


# --- Imagery --------------------------------------------------------------------------------


@router.post(f"{_PREFIX}/locations/{{location_id}}/images/{{kind}}", response_model=PublishingProfileRead, status_code=201)
def add_publishing_image(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    kind: PublishingImageKind,
    image: bytes = Body(
        media_type="image/jpeg",
        description="The photograph itself: JPEG or PNG, at least 800 px on its long edge, at most 10 MiB. "
        "Judged by its bytes, not by the Content-Type.",
    ),
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("publishing_profile")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    location = get_location_or_404(db, dealership_id=dealership_id, location_id=location_id)
    profile = _locked_profile(db, dealership_id, if_match)
    service.add_image(db, store, profile=profile, location=location, kind=kind, data=image, actor_id=principal.user_id)
    return profile_read(db, dealership)


@router.put(f"{_PREFIX}/locations/{{location_id}}/images/{{kind}}/order", response_model=PublishingProfileRead)
def reorder_publishing_images(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    kind: PublishingImageKind,
    body: ImageOrderUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("publishing_profile")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    location = get_location_or_404(db, dealership_id=dealership_id, location_id=location_id)
    profile = _locked_profile(db, dealership_id, if_match)
    service.reorder_images(
        db, profile=profile, location=location, kind=kind, ordered_ids=body.image_ids, actor_id=principal.user_id
    )
    return profile_read(db, dealership)


@router.post(f"{_PREFIX}/images/{{image_id}}/primary", response_model=PublishingProfileRead)
def set_primary_publishing_image(
    dealership_id: uuid.UUID,
    image_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("publishing_profile")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    profile = _locked_profile(db, dealership_id, if_match)
    service.set_primary_image(db, profile=profile, image_id=image_id, actor_id=principal.user_id)
    return profile_read(db, dealership)


@router.get(f"{_PREFIX}/images/{{image_id}}")
def get_publishing_image(
    dealership_id: uuid.UUID,
    image_id: uuid.UUID,
    principal: Principal = Depends(require_read("publishing_profile")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
) -> Response:
    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)
    stored = service.read_image(db, store, dealership_id=dealership_id, image_id=image_id)
    return Response(content=stored.data, media_type=stored.content_type, headers=_FILE_HEADERS)


@router.delete(f"{_PREFIX}/images/{{image_id}}", response_model=PublishingProfileRead)
def delete_publishing_image(
    dealership_id: uuid.UUID,
    image_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("publishing_profile")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    profile = _locked_profile(db, dealership_id, if_match)
    service.remove_image(db, store, profile=profile, image_id=image_id, actor_id=principal.user_id)
    return profile_read(db, dealership)


# --- Lead routing ---------------------------------------------------------------------------


@router.put(f"{_PREFIX}/locations/{{location_id}}/lead-routing", response_model=PublishingProfileRead)
def set_lead_routing(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    body: LeadRoutingUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("publishing_profile")),
    db: Session = Depends(get_db),
):
    """Replaces the site's lead routing. Its lead e-mail is the location's
    own column (the Locations master data): saved here, it moves the
    location's version too."""

    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    profile = _locked_profile(db, dealership_id, if_match)
    location = get_location_for_update_or_404(db, dealership_id=dealership_id, location_id=location_id)
    service.set_lead_routing(db, profile=profile, location=location, data=body, actor_id=principal.user_id)
    return profile_read(db, dealership)
