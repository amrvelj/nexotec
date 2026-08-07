"""Customer: a person or business who is a counterparty to a dealership
transaction (buyer, lessee, service customer) — not an internal User/
employee. Tenant-owned, unlike the tenant-agnostic Vehicle profile (spec §1
IDs & tenant ownership: "Customer is not shared cross-tenant in v1").

customer_type gates a set of nullable columns rather than splitting into two
tables (Customer PRD ruling, 2026-08-07, same pattern as Vehicle's
`condition` field): company_name/legal_form/tax_id are business-only,
birth_date/nationality are individual-only. Mutual exclusivity is enforced
at the schema/service boundary (CustomerCreate validator), not a DB CHECK
constraint, matching every other cross-field business rule in this codebase.

CustomerPhone/CustomerEmail (below) hold the PRD's multi-valued contact
data, added *alongside* the original flat `email`/`phone` columns rather
than replacing them — the flat columns already back a live, tested
create/edit flow and an acceptance-test suite (issue #7); retiring them is
a frontend-contract change that belongs with the Phase B form rebuild, not
a backend-only breaking change bundled into this schema slice.
"""

import datetime as dt
import enum

from sqlalchemy import Boolean, Date, Enum as SAEnum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.models.types import GUID, EncryptedString


class CustomerType(str, enum.Enum):
    INDIVIDUAL = "individual"
    BUSINESS = "business"


class LegalForm(str, enum.Enum):
    """Fixed Swiss legal-entity taxonomy (Customer PRD) — hardcoded, not
    reference-data, same reasoning as Dealer.FranchiseType: this is a legal
    classification, not a per-tenant configurable list.
    """

    AG = "ag"
    GMBH = "gmbh"
    EINZELFIRMA = "einzelfirma"
    VEREIN = "verein"
    GENOSSENSCHAFT = "genossenschaft"
    WEITERE = "weitere"


class PreferredChannel(str, enum.Enum):
    """Customer PRD's contact-preference field — distinct from
    PreferredContactMethod (the original shell field, still present):
    that one names a contact *detail* (email/phone/sms) already on this
    record, this one names a broader outreach *channel* (Mail/Call/Message/
    Letter) per the PRD sheet. Kept as two separate fields rather than
    merged/reconciled, since the value sets don't map 1:1 (e.g. "Letter" has
    no equivalent contact-detail field) — flagged to PM/CTO as a likely
    overlap worth resolving, not resolved silently here.
    """

    MAIL = "mail"
    CALL = "call"
    MESSAGE = "message"
    LETTER = "letter"


class PhoneType(str, enum.Enum):
    MOBILE = "mobile"
    PRIVATE = "private"
    OFFICE = "office"


class EmailType(str, enum.Enum):
    PRIVATE = "private"
    BUSINESS = "business"


class PreferredContactMethod(str, enum.Enum):
    EMAIL = "email"
    PHONE = "phone"
    SMS = "sms"


class CustomerLifecycleStatus(str, enum.Enum):
    PROSPECT = "prospect"
    ACTIVE = "active"
    INACTIVE = "inactive"
    MERGED = "merged"
    DO_NOT_CONTACT = "do_not_contact"


class CustomerSource(str, enum.Enum):
    WALK_IN = "walk_in"
    PHONE = "phone"
    WEB_LEAD = "web_lead"
    MARKETPLACE = "marketplace"
    OTHER = "other"


class Customer(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "customer"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_customer_tenant_id_email"),)

    # Overrides TenantScopedMixin's bare column to add the FK to dealer.id,
    # same as User (app/models/user.py) — the shell's precedent for a
    # tenant-owned entity.
    tenant_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("dealer.id"), nullable=False, index=True)

    customer_type: Mapped[CustomerType] = mapped_column(
        SAEnum(CustomerType, native_enum=False, length=32), nullable=False, default=CustomerType.INDIVIDUAL
    )
    # Individual-only; nullable since a BUSINESS customer has no
    # first/last name (company_name below instead). Mutual exclusivity by
    # customer_type is enforced at the schema/service boundary.
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Individual-only.
    birth_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # Business-only.
    company_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    legal_form: Mapped[LegalForm | None] = mapped_column(
        SAEnum(LegalForm, native_enum=False, length=32), nullable=True
    )
    # Encrypted at rest (EncryptedString), same as Dealer.tax_id — CTO
    # ruling, 2026-08-07: consistency, cheap to apply.
    tax_id: Mapped[str | None] = mapped_column(EncryptedString(), nullable=True)
    preferred_channel: Mapped[PreferredChannel | None] = mapped_column(
        SAEnum(PreferredChannel, native_enum=False, length=16), nullable=True
    )
    # At least one of email/phone is required — enforced at the schema/service
    # boundary (CustomerCreate validator + update_customer), not a DB CHECK
    # constraint, matching the codebase's existing business-rule convention
    # (e.g. Dealer/User lifecycle rules).
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Swiss address, optional at creation (all-or-nothing: either every
    # sub-field is provided or none are — see schemas.customer.CustomerCreate).
    address_street: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_house_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address_postal_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
    address_locality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    address_canton: Mapped[str | None] = mapped_column(String(2), nullable=True)
    address_country: Mapped[str | None] = mapped_column(String(2), nullable=True)

    preferred_contact_method: Mapped[PreferredContactMethod | None] = mapped_column(
        SAEnum(PreferredContactMethod, native_enum=False, length=16), nullable=True
    )
    lifecycle_status: Mapped[CustomerLifecycleStatus] = mapped_column(
        SAEnum(CustomerLifecycleStatus, native_enum=False, length=32),
        nullable=False,
        default=CustomerLifecycleStatus.PROSPECT,
    )
    source: Mapped[CustomerSource | None] = mapped_column(
        SAEnum(CustomerSource, native_enum=False, length=32), nullable=True
    )
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Self-FK, set on merge — points at the surviving record this one was
    # merged into. Only ever written by POST /v1/customers/{id}/merge.
    duplicate_of_customer_id: Mapped[GUID | None] = mapped_column(GUID(), ForeignKey("customer.id"), nullable=True)

    # No enforcement/logging beyond standard audit (Swiss addendum Round 2
    # Q4 #8: TCPA/CAN-SPAM consent capture dropped from v1 acceptance
    # criteria) — field stays, default false, no method/timestamp tracking.
    marketing_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    @property
    def address(self) -> dict[str, str | None] | None:
        """Read-side convenience: flat address_* columns as a nested dict,
        matching app.schemas.customer.CustomerRead. None when no address was
        ever set (address is optional at creation, unlike Dealer's).
        """

        if self.address_street is None:
            return None
        return {
            "street": self.address_street,
            "house_number": self.address_house_number,
            "postal_code": self.address_postal_code,
            "locality": self.address_locality,
            "canton": self.address_canton,
            "country": self.address_country,
        }


class CustomerPhone(PrimaryKeyMixin, TimestampMixin, Base):
    """Multi-valued phone numbers (Customer PRD, 2026-08-07 CTO ruling).
    tenant_id is denormalized from Customer.tenant_id at insert time, same
    reasoning as every other tenant-scoped child table — Postgres unique
    constraints can't be enforced across a join.
    """

    __tablename__ = "customer_phone"
    __table_args__ = (UniqueConstraint("customer_id", "phone_e164", name="uq_customer_phone_customer_id_e164"),)

    tenant_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("dealer.id"), nullable=False, index=True)
    customer_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("customer.id"), nullable=False, index=True)
    phone_type: Mapped[PhoneType] = mapped_column(SAEnum(PhoneType, native_enum=False, length=16), nullable=False)
    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False)
    # Exactly one true per customer — enforced in the service layer (unset
    # the previous primary in the same transaction), not a DB constraint;
    # this isn't a high-contention field (CTO ruling).
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CustomerEmail(PrimaryKeyMixin, TimestampMixin, Base):
    """Multi-valued email addresses — same shape/reasoning as CustomerPhone."""

    __tablename__ = "customer_email"
    __table_args__ = (
        UniqueConstraint("customer_id", "email_address", name="uq_customer_email_customer_id_address"),
    )

    tenant_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("dealer.id"), nullable=False, index=True)
    customer_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("customer.id"), nullable=False, index=True)
    email_type: Mapped[EmailType] = mapped_column(SAEnum(EmailType, native_enum=False, length=16), nullable=False)
    email_address: Mapped[str] = mapped_column(String(254), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CustomerExternalId(PrimaryKeyMixin, TimestampMixin, Base):
    """Per-dealer CRM/OEM system linkage (Customer PRD "external IDs, settable
    by system administrator"). Write access is platform_admin-only (Anto's
    ruling, 2026-08-07, overriding the earlier dealer_admin default) — read
    stays open to any authenticated tenant role, same pattern as an audit-log
    endpoint being readable but not writable by regular roles.

    system_name is a plain validated string, not backed by a lookup table —
    each dealer names their own CRM/OEM systems independently, and this is
    the opposite of ReferenceList's platform_admin-write/globally-shared
    model (CTO correction, 2026-08-07: don't extend that framework here).
    """

    __tablename__ = "customer_external_id"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "system_name", "external_id", name="uq_customer_external_id_tenant_system_external"
        ),
        UniqueConstraint("customer_id", "system_name", name="uq_customer_external_id_customer_system"),
    )

    tenant_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("dealer.id"), nullable=False, index=True)
    customer_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("customer.id"), nullable=False, index=True)
    system_name: Mapped[str] = mapped_column(String(100), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
