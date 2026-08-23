"""Customer request/response schemas.

Phase B contract change (Customer PRD v1.0). The flat `email`/`phone`
fields and `preferredContactMethod` are removed from every schema here:
CustomerPhone/CustomerEmail are the single source of truth for contact
details, and `preferredChannel` is the only contact-preference field
(decisions D-03, D-04). This is a deliberate breaking change to the API
contract, taken in one migration rather than phased, per Anto's ruling.

The knock-on effect is that CustomerCreate now accepts nested `phones` and
`emails`. It has to: the "at least one contact point" invariant (FR-03) was
previously satisfied by the flat columns, and with those gone there would
otherwise be no way to create a customer that satisfies its own invariant
in a single request. Nested contacts are written in the same transaction as
the customer, so a create either fully succeeds or leaves nothing behind.
"""

import datetime as dt
import uuid

from pydantic import EmailStr, Field, model_validator

from app.core.schemas import CamelModel
from app.core.validators import (
    E164Phone,
    HouseNumber,
    SwissUid,
    validate_postal_code_for_country,
)
from app.customer.models.customer import (
    CustomerLifecycleStatus,
    CustomerSource,
    CustomerType,
    EmailType,
    Language,
    LegalForm,
    PhoneType,
    PreferredChannel,
    Salutation,
)
from app.customer.models.vehicle_party import VehiclePartyRole


class CustomerAddress(CamelModel):
    """Write-side address. Deliberately not DealerAddress: the client never
    supplies `canton`. Dealer's canton is tied to its license/regulatory
    record and is user-entered; Customer's is *derived* from the Swiss
    postal code server-side (D-13) and is therefore read-only — see
    CustomerAddressRead below.
    """

    street: str = Field(max_length=200)
    house_number: HouseNumber = Field(max_length=20)
    postal_code: str = Field(max_length=12)
    locality: str = Field(max_length=100)
    country: str = Field(default="CH", max_length=2)

    @model_validator(mode="after")
    def _validate_postal_code(self) -> "CustomerAddress":
        validate_postal_code_for_country(self.postal_code, self.country)
        return self


class CustomerAddressRead(CustomerAddress):
    """Read-side address: adds the server-derived canton. NULL for foreign
    addresses, and NULL for Swiss ones until the postal-code dataset lands
    (D-09, Phase D).
    """

    canton: str | None = None


class CustomerPhoneCreate(CamelModel):
    phone_type: PhoneType
    phone_e164: E164Phone
    is_primary: bool = False


class CustomerEmailCreate(CamelModel):
    email_type: EmailType
    email_address: EmailStr
    is_primary: bool = False


class CustomerCreate(CamelModel):
    """customer_type is immutable after creation (not settable via
    CustomerUpdate below) — gates which of the individual-only
    (first_name/last_name/birth_date/nationality) vs business-only
    (company_name/legal_form/tax_id) fields apply, enforced by
    _validate_customer_type_fields.

    `customer_number` is absent by design: it is allocated by the server
    (D-02) and is immutable, so a client can neither choose nor change it.
    """

    customer_type: CustomerType = CustomerType.INDIVIDUAL
    # Mandatory (D-01). The frontend pre-fills it from the acting user's UI
    # language, but the server does not infer it — an advisor serving an
    # Italian-speaking customer in a German UI must be able to say so, and a
    # silent default would quietly print contracts in the wrong language.
    language: Language
    salutation: Salutation | None = None
    first_name: str | None = Field(default=None, max_length=100, min_length=1)
    last_name: str | None = Field(default=None, max_length=100, min_length=1)
    birth_date: dt.date | None = None
    nationality: str | None = Field(default=None, max_length=2)
    company_name: str | None = Field(default=None, max_length=200, min_length=1)
    legal_form: LegalForm | None = None
    tax_id: SwissUid | None = Field(
        default=None, description="Write-only; never returned by read endpoints."
    )
    preferred_channel: PreferredChannel | None = None
    phones: list[CustomerPhoneCreate] = Field(default_factory=list)
    emails: list[CustomerEmailCreate] = Field(default_factory=list)
    address: CustomerAddress | None = None
    lifecycle_status: CustomerLifecycleStatus = CustomerLifecycleStatus.PROSPECT
    source: CustomerSource | None = None
    source_ref: str | None = Field(default=None, max_length=255)
    marketing_consent: bool = False

    @model_validator(mode="after")
    def _require_a_contact_point(self) -> "CustomerCreate":
        if not self.phones and not self.emails:
            raise ValueError("At least one phone number or email address is required.")
        return self

    @model_validator(mode="after")
    def _reject_duplicate_contacts(self) -> "CustomerCreate":
        numbers = [p.phone_e164 for p in self.phones]
        if len(numbers) != len(set(numbers)):
            raise ValueError("The same phone number was supplied more than once.")
        addresses = [e.email_address.lower() for e in self.emails]
        if len(addresses) != len(set(addresses)):
            raise ValueError("The same email address was supplied more than once.")
        return self

    @model_validator(mode="after")
    def _at_most_one_primary(self) -> "CustomerCreate":
        if sum(1 for p in self.phones if p.is_primary) > 1:
            raise ValueError("Only one phone number can be marked as primary.")
        if sum(1 for e in self.emails if e.is_primary) > 1:
            raise ValueError("Only one email address can be marked as primary.")
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
    lifecycle_status and audit-logs both source IDs. customer_type and
    customer_number are not settable either (immutable after creation) —
    individual/business-only field mutual exclusivity is checked in the
    service layer against the existing row's customer_type, since it isn't
    known at the schema level for a partial PATCH body.

    Contact details are not editable here either: they are managed through
    the /customers/{id}/phones and /customers/{id}/emails endpoints, which
    own the "exactly one primary" invariant.
    """

    language: Language | None = None
    salutation: Salutation | None = None
    first_name: str | None = Field(default=None, max_length=100, min_length=1)
    last_name: str | None = Field(default=None, max_length=100, min_length=1)
    birth_date: dt.date | None = None
    nationality: str | None = Field(default=None, max_length=2)
    company_name: str | None = Field(default=None, max_length=200, min_length=1)
    legal_form: LegalForm | None = None
    tax_id: SwissUid | None = Field(
        default=None, description="Write-only; never returned by read endpoints."
    )
    preferred_channel: PreferredChannel | None = None
    address: CustomerAddress | None = None
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
    customer_number: str
    customer_type: CustomerType
    language: Language
    salutation: Salutation | None
    first_name: str | None
    last_name: str | None
    birth_date: dt.date | None
    nationality: str | None
    company_name: str | None
    legal_form: LegalForm | None
    # tax_id deliberately absent — write-only, same convention as
    # DealerRead never returning Dealer.tax_id.
    preferred_channel: PreferredChannel | None
    address: CustomerAddressRead | None
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
    # U-07: exact below Settings.count_exact_threshold, "at least total"
    # above it (total_is_estimate=True) — never a full scan on a filtered
    # 100k-row table just to render a footer.
    total: int
    total_is_estimate: bool


class CustomerMergeRequest(CamelModel):
    duplicate_of_customer_id: uuid.UUID


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
    """Reshaped in Phase B (D-07). The previous shape required first_name and
    last_name, which meant a *business* customer among the candidates raised
    a serialisation error — i.e. duplicate detection crashed precisely when
    it found a company. Both name fields are now optional, company_name and
    customer_type are included so the UI can label the row, and the primary
    contact details come along so the advisor can recognise the person
    without opening the record.
    """

    id: uuid.UUID
    customer_number: str
    customer_type: CustomerType
    first_name: str | None = None
    last_name: str | None = None
    company_name: str | None = None
    primary_phone: str | None = None
    primary_email: str | None = None
    lifecycle_status: CustomerLifecycleStatus
    # "exact" when an email address or phone number matched outright,
    # "similar" for a name match. Drives the badge in the duplicate panel.
    match: str


class CustomerDuplicateCandidateList(CamelModel):
    items: list[CustomerDuplicateCandidate]


class CustomerVehicleCreate(CamelModel):
    """role is not offered on update (see CustomerVehicleUpdate) — an
    ownership/keeper/driver change hands is a new row with its own
    effective_from, per FR-10 and VehicleParty's own docstring.
    """

    vehicle_id: uuid.UUID
    role: VehiclePartyRole
    # Defaults to now server-side when omitted (D-12) — most creates are
    # "this relationship starts today", and forcing every caller to compute
    # that themselves is friction for no benefit.
    effective_from: dt.datetime | None = None
    effective_to: dt.datetime | None = None


class CustomerVehicleUpdate(CamelModel):
    effective_from: dt.datetime | None = None
    effective_to: dt.datetime | None = None


class VehiclePartySummary(CamelModel):
    """Just enough for the 360 view's Vehicles tab to render a row — not a
    full VehicleRead, which also carries the custody-visibility-redacted
    `status`/`currentCustodianPartnerId` fields that don't apply to "which
    customer is which party" and would require re-implementing that
    redaction rule here for no reason.
    """

    id: uuid.UUID
    vin: str
    make: str
    model: str
    model_year: int
    trim: str | None


class CustomerVehicleRead(CamelModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    vehicle_id: uuid.UUID
    role: VehiclePartyRole
    effective_from: dt.datetime
    effective_to: dt.datetime | None
    vehicle: VehiclePartySummary
    created_at: dt.datetime
    updated_at: dt.datetime


class CustomerVehiclePage(CamelModel):
    items: list[CustomerVehicleRead]
