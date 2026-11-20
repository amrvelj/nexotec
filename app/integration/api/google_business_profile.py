"""The Google Business Profile channel's binding routes (KAN-226 PR-2;
D-A-32, FR-A-35).

A location is bound by the manager (the `integration_connections` row;
platform staff may support): start the link for one of the Betrieb's
locations, come back through the callback the SPA's
`/business-profile-link/callback` page posts Google's query to, pick the Google
location, or disconnect. The tenant is the token's (`require_tenant_match`, 404
for another Betrieb); the platform tenant is no Betrieb here. The callback's
state names the user and the Betrieb it was started for; anyone else gets the
same 409 `link_expired`.
"""

import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.errors import NotFoundError
from app.core.idempotent_route import IdempotentRoute
from app.core.impersonation import refuse_under_impersonation
from app.core.permissions import may_write, require_read, require_write
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.integration.models.google_business_profile import GbpLocationBinding
from app.integration.schemas.google_business_profile import (
    BusinessProfileBindingPage,
    BusinessProfileBindingRead,
    BusinessProfileGoogleChoicePage,
    BusinessProfileGoogleChoiceRead,
    BusinessProfileGoogleLocationPick,
    BusinessProfileLinkCallback,
    BusinessProfileLinkCreate,
    BusinessProfileLinkOutcomeRead,
    BusinessProfileLinkStartRead,
)
from app.integration.services import gbp_link
from app.platform.public import is_platform_dealership

router = APIRouter(tags=["business-profile"], route_class=IdempotentRoute)

_CAPABILITY = "integration_connections"
_BASE = "/dealerships/{dealership_id}/business-profile"


def _read(binding: GbpLocationBinding) -> BusinessProfileBindingRead:
    return BusinessProfileBindingRead.model_validate(binding, from_attributes=True)


def _choices(choices: list[gbp_link.GoogleChoice]) -> list[BusinessProfileGoogleChoiceRead]:
    return [BusinessProfileGoogleChoiceRead.model_validate(choice, from_attributes=True) for choice in choices]


def _own_dealer_betrieb(db: Session, dealership_id: uuid.UUID, principal: Principal) -> None:
    require_tenant_match(dealership_id, principal)
    if is_platform_dealership(db, dealership_id):
        raise NotFoundError(f"Dealership {dealership_id} was not found.")


@router.get(f"{_BASE}/bindings", response_model=BusinessProfileBindingPage)
def list_bindings(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read(_CAPABILITY)),
    db: Session = Depends(get_db),
):
    _own_dealer_betrieb(db, dealership_id, principal)
    return BusinessProfileBindingPage(
        items=[_read(b) for b in gbp_link.list_bindings(db, dealership_id=dealership_id)],
        configured=gbp_link.client_connection(db) is not None,
    )


@router.post(f"{_BASE}/links", dependencies=[Depends(refuse_under_impersonation)], response_model=BusinessProfileLinkStartRead, status_code=201)
def start_link(
    dealership_id: uuid.UUID,
    body: BusinessProfileLinkCreate,
    principal: Principal = Depends(require_write(_CAPABILITY)),
    db: Session = Depends(get_db),
):
    _own_dealer_betrieb(db, dealership_id, principal)
    url = gbp_link.start_link(
        db, dealership_id=dealership_id, user_id=principal.user_id, location_id=body.location_id
    )
    return BusinessProfileLinkStartRead(authorize_url=url)


@router.post("/integrations/business-profile/link-callback", dependencies=[Depends(refuse_under_impersonation)], response_model=BusinessProfileLinkOutcomeRead)
def complete_link(
    body: BusinessProfileLinkCallback,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    outcome = gbp_link.complete_link(
        db,
        dealership_id=principal.tenant_id,
        user_id=principal.user_id,
        state=body.state,
        code=body.code,
        error=body.error,
        may_connect=may_write(_CAPABILITY, roles=principal.roles, is_dealer_manager=principal.is_dealer_manager),
    )
    return BusinessProfileLinkOutcomeRead(binding=_read(outcome.binding), choices=_choices(outcome.choices))


@router.get(f"{_BASE}/bindings/{{binding_id}}/google-locations", response_model=BusinessProfileGoogleChoicePage)
def list_google_locations(
    dealership_id: uuid.UUID,
    binding_id: uuid.UUID,
    principal: Principal = Depends(require_write(_CAPABILITY)),
    db: Session = Depends(get_db),
):
    _own_dealer_betrieb(db, dealership_id, principal)
    binding = gbp_link.get_binding_or_404(db, dealership_id=dealership_id, binding_id=binding_id)
    choices = gbp_link.list_choices(db, binding=binding, actor_id=principal.user_id)
    return BusinessProfileGoogleChoicePage(items=_choices(choices))


@router.put(f"{_BASE}/bindings/{{binding_id}}/google-location", response_model=BusinessProfileBindingRead)
def pick_google_location(
    dealership_id: uuid.UUID,
    binding_id: uuid.UUID,
    body: BusinessProfileGoogleLocationPick,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write(_CAPABILITY)),
    db: Session = Depends(get_db),
):
    _own_dealer_betrieb(db, dealership_id, principal)
    binding = gbp_link.get_binding_for_update_or_404(db, dealership_id=dealership_id, binding_id=binding_id)
    check_version(binding.version, if_match, entity_name="Business Profile binding")
    bound = gbp_link.bind_google_location(
        db, binding=binding, account_name=body.account_name, location_name=body.location_name,
        actor_id=principal.user_id,
    )
    return _read(bound)


@router.delete(f"{_BASE}/bindings/{{binding_id}}", dependencies=[Depends(refuse_under_impersonation)], status_code=204)
def disconnect_binding(
    dealership_id: uuid.UUID,
    binding_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write(_CAPABILITY)),
    db: Session = Depends(get_db),
):
    _own_dealer_betrieb(db, dealership_id, principal)
    binding = gbp_link.get_binding_for_update_or_404(db, dealership_id=dealership_id, binding_id=binding_id)
    check_version(binding.version, if_match, entity_name="Business Profile binding")
    gbp_link.disconnect(db, binding=binding, actor_id=principal.user_id)
    return Response(status_code=204)
