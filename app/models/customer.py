"""Customer: a person who is a counterparty to a dealership transaction
(buyer, lessee, service customer) — not an internal User/employee. Tenant-
owned, unlike the tenant-agnostic Vehicle profile (spec §1 IDs & tenant
ownership: "Customer is not shared cross-tenant in v1").

customer_type is modeled as a single-member enum (`individual`) rather than
the spec table's [individual, business] — the smallest-buildable v1 scope
explicitly defers the business path ("business_name path deferred"), and
there's no business_name field to back a `business` value yet. Keeping the
enum (instead of dropping the field) preserves the schema hook for a
follow-up issue to add BUSINESS + business_name without an API rename; a
`business` value with nothing to fill it in would be a half-finished
feature, not a hook.

No date_of_birth / tax_id_last4 fields — explicitly out of Customer MDM
scope per issue #4 (Finance module territory).
"""

import enum

from sqlalchemy import Boolean, Enum as SAEnum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.models.types import GUID


class CustomerType(str, enum.Enum):
    INDIVIDUAL = "individual"


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
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
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
