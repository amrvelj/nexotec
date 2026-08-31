"""The ONE endpoint anywhere that can return a raw provider payload (WP-6
PR-6). Platform_admin only, always audit-logged with the caller's own
reason, and always attempts to notify the connection's dealer manager —
"break-glass" means every use leaves two trails: the audit log (this
codebase's own generic mechanism, no new schema) and a real notification
to the people whose data was just read.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.auth import Principal, require_access_role
from app.core.errors import NotFoundError
from app.db import get_db
from app.integration.models.call_log import IntegrationCallLog
from app.integration.schemas.call_payload import CallPayloadRead
from app.integration.services import connections as connection_service
from app.integration.services import notifications as notification_service
from app.integration.services import retention as retention_service

router = APIRouter(tags=["integrations"])


def _connection_id_for(db: Session, *, call_log_id: uuid.UUID) -> uuid.UUID:
    log = db.get(IntegrationCallLog, call_log_id)
    if log is None:
        raise NotFoundError(f"Call log {call_log_id} was not found.")
    return log.connection_id


@router.get("/integrations/call-payloads/{payload_id}", response_model=CallPayloadRead)
def get_call_payload(
    payload_id: uuid.UUID,
    reason: str = Query(..., min_length=1, description="Why this raw payload is being inspected — required."),
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    payload = retention_service.get_call_payload_or_404(db, payload_id=payload_id)

    record_audit_event(
        db, entity_type="integration_call_payload", entity_id=payload.id, tenant_id=payload.tenant_id,
        action="break_glass_read", actor_id=principal.user_id, reason=reason,
    )

    connection_id = _connection_id_for(db, call_log_id=payload.call_log_id)
    connection = connection_service.get_connection_or_404(db, tenant_id=None, connection_id=connection_id)
    notification_service.notify_break_glass_access(db, connection=connection, actor_id=principal.user_id, reason=reason)

    return CallPayloadRead.model_validate(payload, from_attributes=True)
