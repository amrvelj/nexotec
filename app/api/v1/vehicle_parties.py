"""The vehicle screen's two party endpoints, composed here at the composition
root (KAN-634, ADR-131): «Allocate to contact» (FR-V-05) and the holders on
the Vehicle 360 Identity tab (FR-V-16, KAN-140).

Why these routes live in `app/api` and not in `app/vehicle/api`: both are
reads and writes of contact's `VehicleParty` about a vehicle-mdm record.
contact sits above vehicle in the layer order (ADR-131 (5)), so vehicle
never imports contact; this module, which may import every context's
`public`, checks the vehicle through `app.vehicle.public` and hands the rest
to `app.contact.public`. Path, method, tags, request and response shapes
and refusals are unchanged — the handlers and their schemas moved here from
`app/vehicle/api/vehicle_mdm.py`, `app/vehicle/api/vehicle_mdm_detail.py`
and `app/vehicle/schemas/vehicle_mdm.py` with their names kept, so the
OpenAPI document is the same.
"""

import datetime as dt
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import require_write
from app.core.schemas import CamelModel
from app.contact import public as contact
from app.db import get_db
from app.vehicle import public as vehicle

# Two routers so each route keeps the OpenAPI tag it had under vehicle.
allocation_router = APIRouter(tags=["vehicle-mdm"], route_class=IdempotentRoute)
holders_router = APIRouter(tags=["vehicle-mdm-detail"], route_class=IdempotentRoute)


class VehicleAllocatePartyRequest(CamelModel):
    """FR-V-05: "Allocate to contact" — the vehicle-side entry point.
    Same allocate_vehicle_party call the contact-side dialog uses
    (Contacts FR-19) — one dialog, not two that drift.
    """

    contact_id: uuid.UUID
    role: contact.VehiclePartyRole


class VehiclePartyAllocationRead(CamelModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID
    contact_id: uuid.UUID
    role: contact.VehiclePartyRole
    effective_from: dt.datetime
    effective_to: dt.datetime | None
    # KAN-140: the holder's name, resolved live inside the contact context
    # (contact.public) within the caller's group — never stored here, so
    # no three-column label. Optional: an additive contract change.
    display_name: str | None = None


@allocation_router.post(
    "/vehicle-mdm/{vehicle_id}/allocate", response_model=VehiclePartyAllocationRead, status_code=201
)
def allocate_to_contact(
    vehicle_id: uuid.UUID,
    body: VehicleAllocatePartyRequest,
    principal: Principal = Depends(require_write("vehicle_mdm")),
    db: Session = Depends(get_db),
):
    """FR-V-05's vehicle-side entry point — "Allocate to contact", the
    Vehicle 360 detail screen's alternative action (ADR-061). Calls
    exactly the same app.contact.public.allocate_vehicle_party the
    contact-side dialog uses, so the close-then-open semantics (ADR-064)
    are identical regardless of which record the user started from.

    Unlike the contact-side dialog, `body.contact_id` here names a
    contact the caller has not already reached through a group-scoped
    path — the contact-side route resolves `contact` via
    `get_contact_or_404(principal.group_id, ...)` before it ever calls
    `allocate_vehicle_party`, so it can never name another group's
    contact. This route must do the same lookup itself, or a caller
    could allocate a party against any contact_id it can guess,
    regardless of which group owns it. VehicleParty carries no group_id
    of its own to catch this at the write; a contact from another group
    is a 404, per rule #7, never a 403 and never a silent cross-group
    write.

    KAN-673: a `vehicle_id` that names a merged record is not refused. The
    allocation is carried out on the record it was merged into
    (`allocate_vehicle_party` resolves it under its lock), and the response's
    `vehicleId` names that survivor. An unknown id is the 404 below, as before.
    """

    vehicle.get_vehicle_mdm_or_404(db, vehicle_id)  # 404s before touching contact at all
    holder = contact.get_contact_or_404(db, principal.group_id, body.contact_id)
    party = contact.allocate_vehicle_party(
        db, vehicle_id=vehicle_id, contact_id=body.contact_id, role=body.role,
        group_id=principal.group_id, actor_id=principal.user_id, acting_dealership_id=principal.tenant_id,
    )
    return VehiclePartyAllocationRead.model_validate(party, from_attributes=True).model_copy(
        update={"display_name": contact.contact_display_name(holder)}
    )


@holders_router.get("/vehicle-mdm/{vehicle_id}/party-roles", response_model=list[VehiclePartyAllocationRead])
def list_party_roles(
    vehicle_id: uuid.UUID,
    include_closed: bool = False,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """FR-V-16: current owner/keeper/driver, plus former allocations on
    request (include_closed=True) — never mixed into the default view.

    Scoped by the caller's own group_id (ADR-014/ADR-049): vehicle_mdm is
    a deliberately global fact, so a party row here may belong to a
    contact in a different dealer group entirely — see
    list_vehicle_parties' own docstring. The holder's name (KAN-140)
    comes from the same group-scoped read, never from a wider lookup.
    """

    vehicle.get_vehicle_mdm_or_404(db, vehicle_id)
    holders = contact.list_vehicle_party_holders(
        db, vehicle_id=vehicle_id, group_id=principal.group_id, include_closed=include_closed
    )
    return [
        VehiclePartyAllocationRead.model_validate(party, from_attributes=True).model_copy(
            update={"display_name": display_name}
        )
        for party, display_name in holders
    ]
