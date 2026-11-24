"""DealerGroup admin endpoints (WP-3 PR-4), composed here at the composition
root (KAN-629, ADR-131). Minimal by design — no group CRUD exists yet; this
is only the one real admin action ADR-030 requires: flipping the group-read
feature flag once a legal-basis record exists.

Why this route lives in `app/api` and not in `app/platform/api`: the flip is
platform's write, but its precondition — has a legal basis ever been
recorded for the group? — is contact's fact, and contact sits above
platform in the layer order (ADR-131). platform never imports contact, so
the route, which may import every context, asks contact and passes the
answer into platform's service. Path, method, response and refusals are
unchanged.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.contact.public import has_any_basis_for_group
from app.core.auth import Principal, require_access_role
from app.core.idempotent_route import IdempotentRoute
from app.db import get_db
from app.platform import public as platform

router = APIRouter(tags=["dealer-groups"], route_class=IdempotentRoute)


@router.post("/dealer-groups/{dealer_group_id}/enable-group-read", response_model=platform.DealerGroupRead)
def enable_group_read(
    dealer_group_id: uuid.UUID,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    dealer_group = platform.get_dealer_group_or_404(db, dealer_group_id)
    return platform.enable_group_read(
        db,
        dealer_group_id=dealer_group.id,
        actor_id=principal.user_id,
        legal_basis_recorded=has_any_basis_for_group(db, group_id=dealer_group.id),
    )
