"""DealerGroup admin endpoints (WP-3 PR-4). Minimal by design — no group
CRUD exists yet (nothing in this WP needs it); this is only the one real
admin action ADR-030 requires: flipping the group-read feature flag once a
legal-basis record exists.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal, require_access_role
from app.db import get_db
from app.platform.schemas.dealership import DealerGroupRead
from app.platform.services import dealership as dealership_service

router = APIRouter(tags=["dealer-groups"])


@router.post("/dealer-groups/{dealer_group_id}/enable-group-read", response_model=DealerGroupRead)
def enable_group_read(
    dealer_group_id: uuid.UUID,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    dealer_group = dealership_service.get_dealer_group_or_404(db, dealer_group_id)
    dealer_group = dealership_service.enable_group_read(db, dealer_group=dealer_group, actor_id=principal.user_id)
    return DealerGroupRead.model_validate(dealer_group, from_attributes=True)
