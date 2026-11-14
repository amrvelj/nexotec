"""Contact groups — Dealer Administration › Kontaktgruppen,
`/administration/contact-groups` (KAN-466; Anto, 2026-10-26).

Nested under /dealerships/{dealership_id}: tenant from the token
(`require_tenant_match`, 404-not-403 on another Betrieb's path id). Any role
reads — the contact pickers need the Betrieb's switched-on groups — and the
manager (or platform staff) switches a group on or off: the matrix row
«Dealership settings», since this is the Betrieb's own configuration
(A-RULE-0). The list itself is platform reference data, written only on
/v1/reference-data/contact_group by platform staff.

The switch is a PUT of the one group's state: idempotent, the same state
twice changes nothing, so it needs no If-Match (there is no version to
race on — the row exists or it does not).
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import require_read, require_write
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.schemas.contact_group import ContactGroupList, ContactGroupRead, ContactGroupSwitch
from app.platform.services import contact_group as contact_group_service
from app.platform.services.contact_group import ContactGroupOption
from app.platform.services.dealership import get_dealership_or_404

router = APIRouter(tags=["contact-groups"], route_class=IdempotentRoute)


def _read(option: ContactGroupOption) -> ContactGroupRead:
    value = option.value
    return ContactGroupRead(
        value_code=value.value_code,
        label_de=value.label_de,
        label_fr=value.label_fr,
        label_it=value.label_it,
        label_en=value.label_en,
        sort_order=value.sort_order,
        active=value.active,
        enabled=option.enabled,
    )


@router.get("/dealerships/{dealership_id}/contact-groups", response_model=ContactGroupList)
def list_contact_groups(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_settings")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    get_dealership_or_404(db, dealership_id)
    return ContactGroupList(items=[_read(o) for o in contact_group_service.list_contact_groups(db, dealership_id)])


@router.put("/dealerships/{dealership_id}/contact-groups/{value_code}", response_model=ContactGroupRead)
def set_contact_group(
    dealership_id: uuid.UUID,
    value_code: str,
    body: ContactGroupSwitch,
    principal: Principal = Depends(require_write("dealership_settings")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    get_dealership_or_404(db, dealership_id)
    option, _changed = contact_group_service.set_contact_group_enabled(
        db, dealership_id=dealership_id, value_code=value_code, enabled=body.enabled, actor_id=principal.user_id
    )
    return _read(option)
