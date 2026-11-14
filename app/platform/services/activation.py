"""«Betrieb aktivieren» (KAN-214; Dealer Administration FR-A-34, D-A-21,
FR-A-39, D-A-34, A-RULE-3; Anto, 2026-11-13: Q3 A, Q4 A).

Activation is the manager's explicit step from `pending_onboarding` to
`active` — never an automatic flip, and never a PATCH of `status` (KAN-712
refuses that with `use_activation_procedure`). It needs:

- every applicable checklist item complete (`services/setup_checklist.py`) —
  no exception, not even for a Betrieb going live mid-month (Q4 A);
- the current ToS and DPA accepted for the Betrieb
  (`services/legal_documents.py`);
- in production, counsel's texts: a current version below 1.0 refuses
  activation (Q3 A).

In one transaction, under the dealership row lock the caller took: every
number range still unfrozen is frozen (A-RULE-3 — activation is the moment
the ranges freeze, whether or not they handed out a number), the status
becomes `active` with `activated_at` / `activated_by`, the audit row is
written, and the fact `platform.dealership.activated` goes to the outbox.
Nothing consumes the fact yet; the hosted website (FR-A-34's «becomes
visible») will.

The number-range confirmation that completes the checklist item is the
ranges' own (`organisation/services/number_range.py::confirm_ranges`).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import ConflictError
from app.core.outbox import OutboxEvent, publish
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.organisation.models.number_range import NumberRange
from app.platform.organisation.services import number_range as number_range_service
from app.platform.services import legal_documents, setup_checklist

ACTIVATED_EVENT_TYPE = "platform.dealership.activated"
ACTIVATION_REASON = "dealership_activated"


def _freeze_number_ranges(db: Session, *, dealership: Dealership, actor_id: uuid.UUID, now) -> int:
    rows = db.scalars(
        select(NumberRange)
        .where(NumberRange.tenant_id == dealership.id, NumberRange.frozen_at.is_(None))
        .order_by(NumberRange.document_type)
        .with_for_update()
    ).all()
    for row in rows:
        row.frozen_at = now
        row.version += 1
        row.updated_by = actor_id
        record_audit_event(
            db,
            entity_type=number_range_service.ENTITY_TYPE,
            entity_id=row.id,
            tenant_id=dealership.id,
            action="number_range_frozen",
            actor_id=actor_id,
            after={"documentType": row.document_type.value, "frozenAt": now.isoformat()},
            reason="activation",
        )
    return len(rows)


def activate(db: Session, *, dealership: Dealership, actor_id: uuid.UUID) -> Dealership:
    """The caller holds the dealership row locked and checked its version.
    Refused with 409 and a stable `details.reason`:

    - `dealership_not_pending` — the Betrieb is not `pending_onboarding`;
    - `setup_checklist_incomplete` — `details.missing` lists the item codes;
    - `legal_documents_unavailable` — no current ToS or DPA is published;
    - `legal_documents_not_final` — production, and a current version is a draft;
    - `legal_acceptance_missing` — `details.documents` lists the types not accepted.

    Never commits."""

    if dealership.status != DealershipStatus.PENDING_ONBOARDING:
        raise ConflictError(
            "Only a Betrieb that is being set up can be activated.",
            details={"reason": "dealership_not_pending", "currentStatus": dealership.status.value},
        )
    checklist = setup_checklist.derive(db, dealership)
    if not checklist.complete:
        raise ConflictError(
            "The setup checklist is not complete.",
            details={
                "reason": "setup_checklist_incomplete",
                "missing": [item.code for item in checklist.missing],
            },
        )
    versions = legal_documents.current_versions(db)
    state = legal_documents.acceptance_state(db, dealership)
    if not state.complete_set:
        raise ConflictError(
            "The Terms of Service and the Data Processing Agreement are not published yet.",
            details={"reason": "legal_documents_unavailable"},
        )
    if legal_documents.drafts_block_activation(versions):
        raise ConflictError(
            "Activation waits for the final Terms of Service and Data Processing Agreement.",
            details={"reason": "legal_documents_not_final"},
        )
    if state.is_due:
        raise ConflictError(
            "The current Terms of Service and Data Processing Agreement must be accepted first.",
            details={
                "reason": "legal_acceptance_missing",
                "documents": [current.document_type.value for current in state.due],
            },
        )

    now = utcnow()
    frozen = _freeze_number_ranges(db, dealership=dealership, actor_id=actor_id, now=now)
    before_status = dealership.status.value
    dealership.status = DealershipStatus.ACTIVE
    dealership.activated_at = now
    dealership.activated_by = actor_id
    dealership.version += 1
    dealership.updated_by = actor_id
    record_audit_event(
        db,
        entity_type="dealership",
        entity_id=dealership.id,
        tenant_id=dealership.id,
        action="activate",
        actor_id=actor_id,
        before={"status": before_status},
        after={
            "status": DealershipStatus.ACTIVE.value,
            "activatedAt": now.isoformat(),
            "numberRangesFrozen": frozen,
            "legalDocuments": {current.document_type.value: current.version for current in versions.values()},
        },
        reason=ACTIVATION_REASON,
    )
    publish(
        db,
        OutboxEvent(
            event_type=ACTIVATED_EVENT_TYPE,
            tenant_id=dealership.id,
            producer="platform",
            aggregate_type="dealership",
            aggregate_id=dealership.id,
            payload={
                "dealershipId": str(dealership.id),
                "activatedAt": now.isoformat(),
                "activatedBy": str(actor_id),
            },
            occurred_at=now,
        ),
    )
    db.flush()
    return dealership
