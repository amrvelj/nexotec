import datetime as dt
import uuid

from pydantic import EmailStr, Field, model_validator

from app.models.customer import CustomerLifecycleStatus, CustomerSource, CustomerType, PreferredContactMethod
from app.schemas.base import CamelModel
from app.schemas.dealer import DealerAddress as CustomerAddress  # same Swiss address shape (validators.py intent)
from app.schemas.validators import E164Phone


class CustomerCreate(CamelModel):
    customer_type: CustomerType = CustomerType.INDIVIDUAL
    first_name: str = Field(max_length=100, min_length=1)
    last_name: str = Field(max_length=100, min_length=1)
    email: EmailStr | None = None
    phone: E164Phone | None = None
    address: CustomerAddress | None = None
    preferred_contact_method: PreferredContactMethod | None = None
    lifecycle_status: CustomerLifecycleStatus = CustomerLifecycleStatus.PROSPECT
    source: CustomerSource | None = None
    source_ref: str | None = Field(default=None, max_length=255)
    marketing_consent: bool = False

    @model_validator(mode="after")
    def _require_email_or_phone(self) -> "CustomerCreate":
        if not self.email and not self.phone:
            raise ValueError("At least one of email or phone is required.")
        return self

    @model_validator(mode="after")
    def _reject_merged_at_creation(self) -> "CustomerCreate":
        if self.lifecycle_status == CustomerLifecycleStatus.MERGED:
            raise ValueError("A new customer cannot be created with lifecycle_status 'merged'.")
        return self


class CustomerUpdate(CamelModel):
    """duplicate_of_customer_id is not settable here — only through
    POST /v1/customers/{id}/merge, which sets it atomically with
    lifecycle_status and audit-logs both source IDs.
    """

    first_name: str | None = Field(default=None, max_length=100, min_length=1)
    last_name: str | None = Field(default=None, max_length=100, min_length=1)
    email: EmailStr | None = None
    phone: E164Phone | None = None
    address: CustomerAddress | None = None
    preferred_contact_method: PreferredContactMethod | None = None
    lifecycle_status: CustomerLifecycleStatus | None = None
    source: CustomerSource | None = None
    source_ref: str | None = Field(default=None, max_length=255)
    marketing_consent: bool | None = None

    @model_validator(mode="after")
    def _reject_merged_via_patch(self) -> "CustomerUpdate":
        if self.lifecycle_status == CustomerLifecycleStatus.MERGED:
            raise ValueError("lifecycle_status 'merged' can only be set via POST /v1/customers/{id}/merge.")
        return self


class CustomerRead(CamelModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    customer_type: CustomerType
    first_name: str
    last_name: str
    email: str | None
    phone: str | None
    address: CustomerAddress | None
    preferred_contact_method: PreferredContactMethod | None
    lifecycle_status: CustomerLifecycleStatus
    source: CustomerSource | None
    source_ref: str | None
    duplicate_of_customer_id: uuid.UUID | None
    marketing_consent: bool
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


class CustomerPage(CamelModel):
    items: list[CustomerRead]
    next_cursor: str | None


class CustomerMergeRequest(CamelModel):
    duplicate_of_customer_id: uuid.UUID


class CustomerDuplicateCandidate(CamelModel):
    id: uuid.UUID
    first_name: str
    last_name: str
    email: str | None
    phone: str | None


class CustomerDuplicateCandidateList(CamelModel):
    items: list[CustomerDuplicateCandidate]
