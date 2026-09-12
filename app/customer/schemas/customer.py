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
from decimal import Decimal

from pydantic import EmailStr, Field, field_validator, model_validator

from app.core.schemas import CamelModel
from app.core.validators import (
    E164Phone,
    HouseNumber,
    Iban,
    SwissUid,
    validate_postal_code_for_country,
)
from app.customer.models.customer import (
    AddressType,
    ConsentScope,
    ConsentSource,
    CustomerLifecycleStatus,
    CustomerSource,
    CustomerType,
    EmailType,
    Gender,
    Language,
    LegalForm,
    PaymentTerms,
    PhoneType,
    PreferredChannel,
    Salutation,
)
from app.customer.models.vehicle_party import VehiclePartyRole


def _require_scope_on_grant(granted: bool | None, scope: ConsentScope | None) -> None:
    """FR-23 §1 — a NEW consent grant must name what it covers. (A NULL
    scope only ever means `marketing` on a row that predates the field.)"""

    if granted is True and scope is None:
        raise ValueError(
            "A consent grant must name its scope: 'marketing', 'invoicing' or 'service' (FR-23 §1)."
        )

# FR-17 stored-field shared helpers (KAN-50). `website` is "scheme-
# normalised on save": a bare host gets `https://` prepended, an explicit
# non-http(s) scheme is rejected outright. `tags` are free per-group
# labels — trimmed, de-duplicated preserving first-seen order, blanks
# dropped; not a reference list and never tidied into one.
_MAX_TAGS = 50


def _normalise_website(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    if "://" in trimmed:
        if not trimmed.lower().startswith(("http://", "https://")):
            raise ValueError("website must be an http or https URL.")
        return trimmed
    return f"https://{trimmed}"


def _normalise_tags(value: list[str]) -> list[str]:
    seen: list[str] = []
    for raw in value:
        tag = raw.strip()
        if not tag:
            continue
        if len(tag) > 60:
            raise ValueError("A tag cannot be longer than 60 characters.")
        if tag not in seen:
            seen.append(tag)
    if len(seen) > _MAX_TAGS:
        raise ValueError(f"A customer cannot carry more than {_MAX_TAGS} tags.")
    return seen

# --- Contact channels (WP-3 PR-5, ADR-067): customer_phone/email/address
# are three child-record tables, each carrying the same six facts (type,
# label, isPrimary, validFrom/validTo, doNotUse[+reason], consent). Field
# names on CustomerAddress* match the model's own address_* attribute names
# (-> addressStreet etc. in JSON) rather than the old bare street/houseNumber
# shape — this is PRD-Customers' own §Contact Data table naming for a
# customer_address ROW, not the flat Customer-level fields the old shape
# mirrored.


class CustomerPhoneCreate(CamelModel):
    phone_type: PhoneType
    label: str | None = Field(default=None, max_length=60)
    phone_e164: E164Phone
    is_primary: bool = False
    # FR-23 §1 — consent may be captured at customer creation (a signed
    # form at the counter). `consent_scope` is required when granting.
    consent_granted: bool = False
    consent_scope: ConsentScope | None = None
    consent_source: ConsentSource | None = None

    @model_validator(mode="after")
    def _consent_scope_on_grant(self) -> "CustomerPhoneCreate":
        _require_scope_on_grant(self.consent_granted, self.consent_scope)
        return self


class CustomerPhoneUpdate(CamelModel):
    phone_type: PhoneType | None = None
    label: str | None = Field(default=None, max_length=60)
    phone_e164: E164Phone | None = None
    is_primary: bool | None = None
    valid_to: dt.datetime | None = None
    do_not_use: bool | None = None
    do_not_use_reason: str | None = Field(default=None, max_length=500)
    consent_granted: bool | None = None
    consent_scope: ConsentScope | None = None
    consent_source: ConsentSource | None = None

    @model_validator(mode="after")
    def _consent_scope_on_grant(self) -> "CustomerPhoneUpdate":
        _require_scope_on_grant(self.consent_granted, self.consent_scope)
        return self


class CustomerPhoneRead(CamelModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    phone_type: PhoneType
    label: str | None
    phone_e164: str
    is_primary: bool
    valid_from: dt.datetime
    valid_to: dt.datetime | None
    do_not_use: bool
    do_not_use_reason: str | None
    consent_granted: bool
    consent_scope: ConsentScope | None
    consent_source: ConsentSource | None
    consent_timestamp: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime


class CustomerPhonePage(CamelModel):
    items: list[CustomerPhoneRead]


class CustomerEmailCreate(CamelModel):
    email_type: EmailType
    label: str | None = Field(default=None, max_length=60)
    email_address: EmailStr
    is_primary: bool = False
    consent_granted: bool = False
    consent_scope: ConsentScope | None = None
    consent_source: ConsentSource | None = None

    @model_validator(mode="after")
    def _consent_scope_on_grant(self) -> "CustomerEmailCreate":
        _require_scope_on_grant(self.consent_granted, self.consent_scope)
        return self


class CustomerEmailUpdate(CamelModel):
    email_type: EmailType | None = None
    label: str | None = Field(default=None, max_length=60)
    email_address: EmailStr | None = None
    is_primary: bool | None = None
    valid_to: dt.datetime | None = None
    do_not_use: bool | None = None
    do_not_use_reason: str | None = Field(default=None, max_length=500)
    consent_granted: bool | None = None
    consent_scope: ConsentScope | None = None
    consent_source: ConsentSource | None = None

    @model_validator(mode="after")
    def _consent_scope_on_grant(self) -> "CustomerEmailUpdate":
        _require_scope_on_grant(self.consent_granted, self.consent_scope)
        return self


class CustomerEmailRead(CamelModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    email_type: EmailType
    label: str | None
    email_address: str
    is_primary: bool
    valid_from: dt.datetime
    valid_to: dt.datetime | None
    do_not_use: bool
    do_not_use_reason: str | None
    consent_granted: bool
    consent_scope: ConsentScope | None
    consent_source: ConsentSource | None
    consent_timestamp: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime


class CustomerEmailPage(CamelModel):
    items: list[CustomerEmailRead]


class CustomerAddressCreate(CamelModel):
    address_type: AddressType
    label: str | None = Field(default=None, max_length=60)
    address_street: str = Field(max_length=200)
    # FR-17, 2026-08-21 — c/o, department, building, PO box. Exempt from the
    # all-or-nothing address rule below: optional even when every other
    # sub-field is supplied.
    address_line2: str | None = Field(default=None, max_length=200)
    address_house_number: HouseNumber = Field(max_length=20)
    address_postal_code: str = Field(max_length=12)
    address_locality: str = Field(max_length=100)
    # ISO 3166-1 alpha-2. Membership against the `country` reference list is
    # enforced in the service layer (KAN-32) — a schema cannot reach the DB,
    # and `max_length=2` is a width check, not validation.
    address_country: str = Field(default="CH")
    is_primary: bool = False
    consent_granted: bool = False
    consent_scope: ConsentScope | None = None
    consent_source: ConsentSource | None = None

    @model_validator(mode="after")
    def _validate_postal_code(self) -> "CustomerAddressCreate":
        validate_postal_code_for_country(self.address_postal_code, self.address_country)
        return self

    @model_validator(mode="after")
    def _consent_scope_on_grant(self) -> "CustomerAddressCreate":
        _require_scope_on_grant(self.consent_granted, self.consent_scope)
        return self


class CustomerAddressUpdate(CamelModel):
    address_type: AddressType | None = None
    label: str | None = Field(default=None, max_length=60)
    address_street: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    address_house_number: HouseNumber | None = None
    address_postal_code: str | None = Field(default=None, max_length=12)
    address_locality: str | None = Field(default=None, max_length=100)
    # ISO 3166-1 alpha-2 — see CustomerAddressCreate.address_country (KAN-32).
    address_country: str | None = Field(default=None)
    is_primary: bool | None = None
    valid_to: dt.datetime | None = None
    do_not_use: bool | None = None
    do_not_use_reason: str | None = Field(default=None, max_length=500)
    consent_granted: bool | None = None
    consent_scope: ConsentScope | None = None
    consent_source: ConsentSource | None = None

    @model_validator(mode="after")
    def _consent_scope_on_grant(self) -> "CustomerAddressUpdate":
        _require_scope_on_grant(self.consent_granted, self.consent_scope)
        return self


class CustomerAddressRead(CamelModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    address_type: AddressType
    label: str | None
    address_street: str
    address_line2: str | None
    address_house_number: str
    address_postal_code: str
    address_locality: str
    # Derived from the Swiss postal code server-side, same as Customer's own
    # legacy address_canton — NULL for foreign addresses.
    address_canton: str | None
    address_country: str
    is_primary: bool
    valid_from: dt.datetime
    valid_to: dt.datetime | None
    do_not_use: bool
    do_not_use_reason: str | None
    consent_granted: bool
    consent_scope: ConsentScope | None
    consent_source: ConsentSource | None
    consent_timestamp: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime


class CustomerAddressPage(CamelModel):
    items: list[CustomerAddressRead]


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
    # ISO 3166-1 alpha-2, individual-only. Chosen from the `country`
    # reference list; membership is enforced in the service layer (KAN-32),
    # since a schema cannot reach the DB.
    nationality: str | None = Field(default=None)
    company_name: str | None = Field(default=None, max_length=200, min_length=1)
    legal_form: LegalForm | None = None
    tax_id: SwissUid | None = Field(
        default=None, description="Write-only; never returned by read endpoints."
    )
    preferred_channel: PreferredChannel | None = None
    phones: list[CustomerPhoneCreate] = Field(default_factory=list)
    emails: list[CustomerEmailCreate] = Field(default_factory=list)
    addresses: list[CustomerAddressCreate] = Field(default_factory=list)
    lifecycle_status: CustomerLifecycleStatus = CustomerLifecycleStatus.PROSPECT
    source: CustomerSource | None = None
    source_ref: str | None = Field(default=None, max_length=255)
    marketing_consent: bool = False

    # --- FR-17 / FR-18 stored fields (KAN-50) ---
    title: str | None = Field(default=None, max_length=50)  # individual-only, free text
    gender: Gender = Gender.UNSPECIFIED  # individual-only, segmentation-only, never inferred
    website: str | None = Field(default=None, max_length=500)
    newsletter: bool = False
    payment_terms: PaymentTerms | None = None
    # Advisory only in v1 — surfaced, never enforced (FR-18 region 4).
    credit_limit: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    iban: Iban | None = None  # mod-97 validated; a payout destination, never a payment instrument
    vat_registered: bool = False
    # Defaults to the acting user on create (D-24) when omitted — resolved
    # in the service, where the acting user is known.
    advisor_id: uuid.UUID | None = None
    customer_since: dt.date | None = None
    next_follow_up: dt.date | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("website")
    @classmethod
    def _website_scheme(cls, value: str | None) -> str | None:
        return _normalise_website(value)

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        return _normalise_tags(value)

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
        # Per type-group (ADR-067, FR-07) — two contacts of DIFFERENT types
        # may each be primary at once (a primary mobile AND a primary work
        # phone, a primary domicile AND a primary billing address), only two
        # of the SAME type competing is rejected. This is the shape the whole
        # service layer already implements (_default_primary_flags,
        # _fixup_single_primary, create_customer_phone).
        phones_by_type: dict[PhoneType, int] = {}
        for phone in self.phones:
            if phone.is_primary:
                phones_by_type[phone.phone_type] = phones_by_type.get(phone.phone_type, 0) + 1
        if any(count > 1 for count in phones_by_type.values()):
            raise ValueError("Only one phone number per type can be marked as primary.")
        emails_by_type: dict[EmailType, int] = {}
        for email in self.emails:
            if email.is_primary:
                emails_by_type[email.email_type] = emails_by_type.get(email.email_type, 0) + 1
        if any(count > 1 for count in emails_by_type.values()):
            raise ValueError("Only one email address per type can be marked as primary.")
        addresses_by_type: dict[AddressType, int] = {}
        for address in self.addresses:
            if address.is_primary:
                addresses_by_type[address.address_type] = addresses_by_type.get(address.address_type, 0) + 1
        if any(count > 1 for count in addresses_by_type.values()):
            raise ValueError("Only one address per type can be marked as primary.")
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
            if self.title or self.gender != Gender.UNSPECIFIED:
                raise ValueError("title and gender are individual-only fields.")
        return self

    @model_validator(mode="after")
    def _reject_merged_at_creation(self) -> "CustomerCreate":
        if self.lifecycle_status == CustomerLifecycleStatus.MERGED:
            raise ValueError("A new customer cannot be created with lifecycle_status 'merged'.")
        return self


class CustomerCreditBlockRequest(CamelModel):
    """WP-8 PR-6 (ADR-065/S-D19)."""

    blocked: bool
    reason: str | None = None


class CustomerUpdate(CamelModel):
    """duplicate_of_customer_id is not settable here — only through
    POST /v1/customers/{id}/merge, which sets it atomically with
    lifecycle_status and audit-logs both source IDs. customer_type and
    customer_number are not settable either (immutable after creation) —
    individual/business-only field mutual exclusivity is checked in the
    service layer against the existing row's customer_type, since it isn't
    known at the schema level for a partial PATCH body.

    Contact details are not editable here either: they are managed through
    the /customers/{id}/phones, /customers/{id}/emails and
    /customers/{id}/addresses endpoints, which own the "exactly one primary
    per type" invariant (ADR-067, WP-3 PR-5 — extended from "per customer"
    to "per type" for phones/emails at the same time addresses joined them).
    """

    language: Language | None = None
    salutation: Salutation | None = None
    first_name: str | None = Field(default=None, max_length=100, min_length=1)
    last_name: str | None = Field(default=None, max_length=100, min_length=1)
    birth_date: dt.date | None = None
    # ISO 3166-1 alpha-2 — see CustomerCreate.nationality (KAN-32).
    nationality: str | None = Field(default=None)
    company_name: str | None = Field(default=None, max_length=200, min_length=1)
    legal_form: LegalForm | None = None
    tax_id: SwissUid | None = Field(
        default=None, description="Write-only; never returned by read endpoints."
    )
    preferred_channel: PreferredChannel | None = None
    lifecycle_status: CustomerLifecycleStatus | None = None
    source: CustomerSource | None = None
    source_ref: str | None = Field(default=None, max_length=255)
    marketing_consent: bool | None = None

    # --- FR-17 / FR-18 stored fields (KAN-50) ---
    # A key present with `null` clears the value (the service reads
    # model_dump(exclude_unset=True), so "absent" and "null" are distinct);
    # `advisor_id: null` is how the UI un-assigns an advisor (D-24), and
    # the create-time default is never re-applied here.
    title: str | None = Field(default=None, max_length=50)
    gender: Gender | None = None
    website: str | None = Field(default=None, max_length=500)
    newsletter: bool | None = None
    payment_terms: PaymentTerms | None = None
    credit_limit: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    iban: Iban | None = None
    vat_registered: bool | None = None
    advisor_id: uuid.UUID | None = None
    customer_since: dt.date | None = None
    next_follow_up: dt.date | None = None
    tags: list[str] | None = None
    notes: str | None = None

    @field_validator("website")
    @classmethod
    def _website_scheme(cls, value: str | None) -> str | None:
        return _normalise_website(value)

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _normalise_tags(value)

    @model_validator(mode="after")
    def _reject_merged_via_patch(self) -> "CustomerUpdate":
        if self.lifecycle_status == CustomerLifecycleStatus.MERGED:
            raise ValueError("lifecycle_status 'merged' can only be set via POST /v1/customers/{id}/merge.")
        return self

    @model_validator(mode="after")
    def _gender_not_cleared(self) -> "CustomerUpdate":
        # gender is NOT NULL on the model (defaults to `unspecified`); to
        # "reset" it a caller sends 'unspecified', never null.
        if "gender" in self.model_fields_set and self.gender is None:
            raise ValueError("gender cannot be cleared — set it to 'unspecified' instead.")
        return self


class CustomerRead(CamelModel):
    id: uuid.UUID
    group_id: uuid.UUID
    customer_number: str
    customer_type: CustomerType
    language: Language
    salutation: Salutation | None
    first_name: str | None
    last_name: str | None
    birth_date: dt.date | None
    nationality: str | None
    title: str | None
    gender: Gender
    company_name: str | None
    legal_form: LegalForm | None
    # tax_id deliberately absent — write-only, same convention as
    # DealershipRead never returning Dealership.tax_id.
    preferred_channel: PreferredChannel | None
    website: str | None
    newsletter: bool
    # Six read-model projections (ADR-067) — computed from customer_phone/
    # email/address child rows, never stored columns. The grid's flat
    # Mobile/Email/Work-phone columns read these, not a Customer field.
    phone_mobile: str | None = None
    phone_landline: str | None = None
    phone_work: str | None = None
    email: str | None = None
    email_secondary: str | None = None
    address: CustomerAddressRead | None = None
    lifecycle_status: CustomerLifecycleStatus
    source: CustomerSource | None
    source_ref: str | None
    duplicate_of_customer_id: uuid.UUID | None
    marketing_consent: bool
    # WP-8 PR-6 (ADR-065/S-D19) — stops a contract, never an offer; see
    # app.customer.services.customer.set_credit_block.
    credit_block: bool
    credit_block_reason: str | None
    credit_blocked_at: dt.datetime | None

    # --- FR-17 / FR-18 stored fields (KAN-50) ---
    payment_terms: PaymentTerms | None
    credit_limit: Decimal | None
    iban: str | None
    vat_registered: bool
    # advisor: the P-2 three-column pattern — id + denormalised label +
    # refresh timestamp. `advisor_id` is a platform User id (no FK).
    advisor_id: uuid.UUID | None
    advisor_label: str | None
    advisor_label_refreshed_at: dt.datetime | None
    customer_since: dt.date | None
    next_follow_up: dt.date | None
    notes: str | None
    tags: list[str] = Field(default_factory=list)
    # Provenance — the dealership that created the record (ADR-014: the
    # GROUP owns the customer). Never scopes a read.
    dealership_id: uuid.UUID | None

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


class CustomerAdvisorOption(CamelModel):
    """One option for the customer record's advisor picker (KAN-50 / D-24)
    — an active user of the ACTING dealership. `label` is the same
    `First Last` denormalised into `Customer.advisor_label` on assignment.
    """

    id: uuid.UUID
    label: str


class CustomerAdvisorOptionList(CamelModel):
    items: list[CustomerAdvisorOption]


class CustomerMergeRequest(CamelModel):
    duplicate_of_customer_id: uuid.UUID


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

    KAN-31: make/model/modelYear are nullable, unlike the legacy Vehicle
    table this replaced — vehicle_mdm carries them only via an OPTIONAL
    catalogue_variant match (VehicleMdm.make/model/trim/model_year, the
    read-only properties that do this resolution). An unmatched vehicle_mdm
    row (catalogue_match_status=unverified) is the common case today, not
    an edge case — every fixture in the existing allocation test suite
    creates one. A full catalogue-aware summary (falling back through
    provider data, say) is ADR-073's job; this is the minimal,
    always-correct version. The frontend falls back to the vehicle number
    when these are null.
    """

    id: uuid.UUID
    vin: str
    # Always present (VehicleMdm.vehicle_number is non-nullable) — the
    # fallback label when make/model are null, same convention the global
    # vehicle search already uses (DmsShell.tsx).
    vehicle_number: str
    make: str | None
    model: str | None
    model_year: int | None
    trim: str | None


class OtherVehiclePartySummary(CamelModel):
    """KAN-49 / FR-19 — the other open party rows on the SAME vehicle, so a
    seller taking a leased company car in trade sees owner/keeper/driver as
    three different people, not just "this customer's car". Live-resolved
    (intra-context: VehicleParty.customer_id -> another Customer row), not
    the three-column denormalization pattern — that pattern is for
    CROSS-context references (rule #2/#3); this is customer -> customer.
    Closed rows are excluded — they are not parties now.
    """

    customer_id: uuid.UUID
    role: VehiclePartyRole
    display_name: str


class VehicleStockLinkSummary(CamelModel):
    """FR-19's 2026-09-07 amendment — a vehicle currently in the group's
    own stock links to its stock item, beside the party roles. Just enough
    to link and label; never price/margin (ADR-029/049 stay private to the
    legal entity, and this read doesn't even fetch them)."""

    id: uuid.UUID
    stock_number: str


class CustomerVehicleRead(CamelModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    vehicle_id: uuid.UUID
    role: VehiclePartyRole
    effective_from: dt.datetime
    effective_to: dt.datetime | None
    vehicle: VehiclePartySummary
    # KAN-49 / FR-19 — populated in a batch pass by the route, not derivable
    # from the ORM row alone; see list_other_vehicle_parties_batch and
    # get_stock_items_for_vehicles. Empty list / null are the common case.
    other_parties: list[OtherVehiclePartySummary] = Field(default_factory=list)
    stock_item: VehicleStockLinkSummary | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class CustomerVehiclePage(CamelModel):
    items: list[CustomerVehicleRead]
