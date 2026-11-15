"""The Betrieb's status page and Nexotec's incidents (KAN-223; Dealer
Administration D-A-38, FR-A-43).

Dealer side: `GET /dealerships/{dealership_id}/status` — the page, derived on
read, readable by every role of the Betrieb (matrix row `status_page`;
tenant from the token, 404 for another Betrieb's id). The header indicator
reads the same endpoint; the SPA shows it to managers only (Anto, 2026-11-14,
Q2 A) — the page itself stays every role's.

Platform side: `GET/POST /platform/status-incidents` and
`PATCH /platform/status-incidents/{id}` — platform staff open, edit and end
Nexotec's own incidents (with a second factor in the session for writes, as
for the legal documents). No external status-page service (D-A-38).
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal, require_access_role, require_mfa_session
from app.core.base import utcnow
from app.core.concurrency import check_version, require_if_match
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import require_read
from app.core.status import StatusState
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.organisation.services.dealership import get_dealership_or_404
from app.platform.schemas.status_page import (
    StatusIncidentCreate,
    StatusIncidentList,
    StatusIncidentRead,
    StatusIncidentUpdate,
    StatusItemRead,
    StatusPageRead,
    incident_read,
)
from app.platform.services import status_incidents, status_page

router = APIRouter(tags=["status-page"], route_class=IdempotentRoute)


@router.get("/dealerships/{dealership_id}/status", response_model=StatusPageRead)
def get_status_page(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("status_page")),
    db: Session = Depends(get_db),
):
    """The Betrieb's status (D-A-38): every item each source derives from its
    own data — number ranges, the integration connections, Nexotec's own
    status — the overall state (green only when every item is) and how many
    items want attention. Nothing on this page is ever set by hand."""

    require_tenant_match(dealership_id, principal)
    get_dealership_or_404(db, dealership_id)
    page = status_page.derive(db, dealership_id, now=utcnow())
    return StatusPageRead(
        dealership_id=dealership_id,
        state=page.state,
        attention_count=sum(1 for item in page.items if item.state != StatusState.OK),
        items=[
            StatusItemRead(
                key=item.key,
                source=item.source,
                state=item.state,
                message_key=item.message_key,
                params=item.params,
                link=item.link,
                incident=incident_read(page.incidents[item.key]) if item.key in page.incidents else None,
            )
            for item in page.items
        ],
    )


# --- Platform Administration › Status --------------------------------------------------------


@router.get("/platform/status-incidents", response_model=StatusIncidentList)
def list_status_incidents(
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    return StatusIncidentList(items=[incident_read(row) for row in status_incidents.list_incidents(db)])


@router.post(
    "/platform/status-incidents",
    response_model=StatusIncidentRead,
    status_code=201,
    dependencies=[Depends(require_mfa_session())],
)
def open_status_incident(
    body: StatusIncidentCreate,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    """Open an incident on Nexotec's platform status: it shows on every
    Betrieb's page from `startsAt` (default now) until `endsAt`. Title and
    text in all four UI languages (422 otherwise). Audited."""

    row = status_incidents.open_incident(
        db,
        severity=body.severity,
        title=body.title.model_dump(),
        body=body.body.model_dump(),
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        actor_id=principal.user_id,
    )
    db.commit()
    db.refresh(row)
    return incident_read(row)


@router.patch(
    "/platform/status-incidents/{incident_id}",
    response_model=StatusIncidentRead,
    dependencies=[Depends(require_mfa_session())],
)
def update_status_incident(
    incident_id: uuid.UUID,
    body: StatusIncidentUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    """Edit an incident or end it (`endsAt`); If-Match on its version.
    Audited with before and after. An incident is never deleted."""

    row = status_incidents.get_incident_or_404(db, incident_id, for_update=True)
    check_version(row.version, if_match, entity_name="Status incident")
    fields = body.model_fields_set
    kwargs: dict = {}
    if "ends_at" in fields:
        kwargs["ends_at"] = body.ends_at
    status_incidents.update_incident(
        db,
        row,
        actor_id=principal.user_id,
        severity=body.severity,
        title=body.title.model_dump() if body.title is not None else None,
        body=body.body.model_dump() if body.body is not None else None,
        starts_at=body.starts_at,
        **kwargs,
    )
    db.commit()
    db.refresh(row)
    return incident_read(row)
