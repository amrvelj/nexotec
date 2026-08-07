import datetime as dt
import uuid

from pydantic import EmailStr, Field, model_validator

from app.models.customer import (
    CustomerLifecycleStatus,
    CustomerSource,
    CustomerType,
    EmailType,
    LegalForm,
    PhoneType,
    PreferredChannel,
    PreferredContactMethod,
)
from app.schemas.base import CamelModel
from app.schemas.validators import E164Phone, SwissPostalCode


class CustomerAddress(CamelModel):
    """Deliberately not DealerAddress: no `canton` field. Dealer's canton is
    tied to its license/regulatory record; Customer has no such requirement,
    and requiring it here was blocking customer creation for no reason
    (product feedback 2026-08-07) — the underlying `address_canton` DB
    column stays nullable and simply goes unused rather than being dropped.
    """

    street: str = Field(max_length=200)
    house_number: str = Field(max_length=20)
    postal_code: SwissPostalCode
    locality: str = Field(max_length=100)
    country: str = Field(default="CH", max_length=2)


class CustomerCreate(CamelModel):
    """customer_type is immutable after creation (not settable via
    CustomerUpdate below) — gates which of the individual-only
    (first_name/last_name/birth_date/nationality) vs business-only
    (company_name/legal_form/tax_id) fields apply, enforced by
    _validate_customer_type_fields.
    """

    customer_type: CustomerType = CustomerType.INDIVIDUAL
    first_name: str | None = Field(default=None, max_length=100, min_length=1)
    last_name: str | None = Field(default=None, max_length=100, min_length=1)
    birth_date: dt.date | None = None
    nationality: str | None = Field(default=None, max_length=2)
    company_name: str | None = Field(default=None, max_length=200, min_length=1)
    legal_form: LegalForm | None = None
    tax_id: str | None = Field(
        default=None, min_length=1, description="Write-only; never returned by read endpoints."
    )
    preferred_channel: PreferredChannel | None = None
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
    def _validate_customer_type_fields(self) -> "CustomerCreate":
        if self.customer_type == CustomerType.INDIVIDUAL:
            if not self.first_name or not self.last_name:
                raise ValueError("first_name and last_name are required for an individual customer.")
            if self.company_name or self.legal_form or self.tax_id:
                raise ValueError("company_name, legal_form, and tax_id are business-only fields.")
        else:
            if not self.company_name:
                raise ValueError("company_name is required for a business customer.")
            if self.first_name or self.last_name or self.birth_date or self.nationality:
                raise ValueError("first_name, last_name, birth_date, and nationality are individual-only fields.")
        return self

    @model_validator(mode="after")
    def _reject_merged_at_creation(self) -> "CustomerCreate":
        if self.lifecycle_status == CustomerLifecycleStatus.MERGED:
            raise ValueError("A new customer cannot be created with lifecycle_status 'merged'.")
        return self


class CustomerUpdate(CamelModel):
    """duplicate_of_customer_id is not settable here — only through
    POST /v1/customers/{id}/merge, which sets it atomically with
    lifecycle_status and audit-logs both source IDs. customer_type is not
    settable either (immutable after creation) — individual/business-only
    field mutual exclusivity is checked in the service layer against the
    existing row's customer_type, since it isn't known at the schema level
    for a partial PATCH body.
    """

    first_name: str | None = Field(default=None, max_length=100, min_length=1)
    last_name: str | None = Field(default=None, max_length=100, min_length=1)
    birth_date: dt.date | None = None
    nationality: str | None = Field(default=None, max_length=2)
    company_name: str | None = Field(default=None, max_length=200, min_length=1)
    legal_form: LegalForm | None = None
    tax_id: str | None = Field(
        default=None, min_length=1, description="Write-only; never returned by read endpoints."
    )
    preferred_channel: PreferredChannel | None = None
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
    first_name: str | None
    last_name: str | None
    birth_date: dt.date | None
    nationality: str | None
    company_name: str | None
    legal_form: LegalForm | None
    # tax_id deliberately absent — write-only, same convention as
    # DealerRead never returning Dealer.tax_id.
    preferred_channel: PreferredChannel | None
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


class CustomerPhoneCreate(CamelModel):
    phone_type: PhoneType
    phone_e164: E164Phone
    is_primary: bool = False


class CustomerPhoneUpdate(CamelModel):
    phone_type: PhoneType | None = None
    phone_e164: E164Phone | None = None
    is_primary: bool | None = None


class CustomerPhoneRead(CamelModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    phone_type: PhoneType
    phone_e164: str
    is_primary: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class CustomerPhonePage(CamelModel):
    items: list[CustomerPhoneRead]


class CustomerEmailCreate(CamelModel):
    email_type: EmailType
    email_address: EmailStr
    is_primary: bool = False


class CustomerEmailUpdate(CamelModel):
    email_type: EmailType | None = None
    email_address: EmailStr | None = None
    is_primary: bool | None = None


class CustomerEmailRead(CamelModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    email_type: EmailType
    email_address: str
    is_primary: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class CustomerEmailPage(CamelModel):
    items: list[CustomerEmailRead]


class CustomerExternalIdCreate(CamelModel):
    system_name: str = Field(max_length=100, min_length=1)
    external_id: str = Field(max_length=255, min_length=1)


class CustomerExternalIdUpdate(CamelModel):
    system_name: str | None = Field(default=None, max_length=100, min_length=1)
    external_id: str | None = Field(default=None, max_length=255, min_length=1)


class CustomerExternalIdRead(CamelModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    system_name: str
    external_id: str
    created_at: dt.datetime
    updated_at: dt.datetime


class CustomerExternalIdPage(CamelModel):
    items: list[CustomerExternalIdRead]


class CustomerDuplicateCandidate(CamelModel):
    id: uuid.UUID
    first_name: str
    last_name: str
    email: str | None
    phone: str | None


class CustomerDuplicateCandidateList(CamelModel):
    items: list[CustomerDuplicateCandidate]
