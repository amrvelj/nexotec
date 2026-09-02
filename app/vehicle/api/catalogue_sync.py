"""PR-7's fleet-wide catalogue sync status — feeds the Integrations
platform view's own health board (a DataGrid, never a bespoke table).
Platform_admin only: this is cross-tenant by nature (rule 7's own
"cross-tenant reads return 404, never 403" is about a SPECIFIC tenant's
data; this endpoint's whole purpose is reading across all of them, so it
is gated the ordinary platform_admin way instead — the same posture
app.integration.api.providers.py already uses for its own admin-only
reads).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal, require_access_role
from app.db import get_db
from app.vehicle.schemas.catalogue_sync import CatalogueSyncStatusRead
from app.vehicle.services import catalogue_sync as catalogue_sync_service

router = APIRouter(tags=["vehicle-mdm"])


@router.get("/vehicle-mdm/catalogue-sync-status", response_model=list[CatalogueSyncStatusRead])
def list_catalogue_sync_status(
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    today = datetime.now(UTC).date()
    states = catalogue_sync_service.list_all_sync_states(db)
    return [
        CatalogueSyncStatusRead(
            tenant_id=state.tenant_id,
            provider_code=state.provider_code,
            last_full_seed_at=state.last_full_seed_at,
            last_delta_cursor=state.last_delta_cursor,
            last_system_watermark_date=state.last_system_watermark_date,
            last_system_checked_at=state.last_system_checked_at,
            stale=catalogue_sync_service.compute_sync_age_alarm(state, today=today),
        )
        for state in states
    ]
