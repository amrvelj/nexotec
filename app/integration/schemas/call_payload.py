"""IntegrationCallPayload schemas (WP-6 PR-6). `CallPayloadRead` is the
ONE schema anywhere in this context that ever carries a decrypted raw
payload — reachable only from the platform_admin-only break-glass
endpoint (api/call_payloads.py), never from a dealer-facing route.
"""

import datetime as dt
import uuid

from app.core.schemas import CamelModel
from app.integration.models.call_payload import PayloadKind


class CallPayloadRead(CamelModel):
    id: uuid.UUID
    call_log_id: uuid.UUID
    tenant_id: uuid.UUID | None
    kind: PayloadKind
    payload: str
    created_at: dt.datetime
