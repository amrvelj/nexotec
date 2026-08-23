import datetime as dt
import uuid
from decimal import Decimal

from pydantic import Field

from app.core.schemas import CamelModel
from app.sales.models.transaction import TransactionStatus, TransactionType


class TransactionCreate(CamelModel):
    """`status` is always `draft` on create (not settable) — the only way
    to reach `completed`/`cancelled` is through the dedicated /complete and
    /cancel actions, same reasoning as Customer's merge endpoint.
    `transaction_date` is not settable either — set on completion, not
    creation (spec §4 Fields).
    """

    transaction_type: TransactionType
    customer_id: uuid.UUID
    vehicle_id: uuid.UUID
    primary_user_id: uuid.UUID
    amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    external_ref: str | None = Field(default=None, max_length=255)
    notes: str | None = None


class TransactionUpdate(CamelModel):
    """Only permitted while `status == draft` — enforced at the service
    layer (spec: PATCH "restricted post-completion"). `customer_id`/
    `vehicle_id`/`primary_user_id` are editable here since a draft deal can
    still change hands before completion.
    """

    transaction_type: TransactionType | None = None
    customer_id: uuid.UUID | None = None
    vehicle_id: uuid.UUID | None = None
    primary_user_id: uuid.UUID | None = None
    amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    external_ref: str | None = Field(default=None, max_length=255)
    notes: str | None = None


class TransactionCancelRequest(CamelModel):
    reason: str | None = None


class TransactionRead(CamelModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    transaction_type: TransactionType
    status: TransactionStatus
    customer_id: uuid.UUID
    vehicle_id: uuid.UUID
    primary_user_id: uuid.UUID
    amount: Decimal | None
    transaction_date: dt.datetime | None
    external_ref: str | None
    notes: str | None
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


class TransactionPage(CamelModel):
    items: list[TransactionRead]
    next_cursor: str | None
