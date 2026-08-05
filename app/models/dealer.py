"""Dealer: the tenant root. Dealer.id is the tenant_id used everywhere else
(spec cross-cutting #1, issue #2). Not TenantScopedMixin — a Dealer IS the
tenant, it doesn't belong to one.
"""

import enum

from sqlalchemy import JSON, Enum as SAEnum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.models.types import GUID, EncryptedString


class FranchiseType(str, enum.Enum):
    FRANCHISE = "franchise"
    INDEPENDENT = "independent"


class DealerStatus(str, enum.Enum):
    PENDING_ONBOARDING = "pending_onboarding"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    OFFBOARDED = "offboarded"


class Dealer(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "dealer"

    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    dba_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    dealer_license_number: Mapped[str] = mapped_column(String(64), nullable=False)
    license_state: Mapped[str] = mapped_column(String(2), nullable=False)
    franchise_type: Mapped[FranchiseType] = mapped_column(
        SAEnum(FranchiseType, native_enum=False, length=32), nullable=False
    )
    oem_affiliations: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    # Swiss address (addendum decision #2): flat columns, not a nested/JSON
    # blob, so it's queryable/validatable like any other required field.
    address_street: Mapped[str] = mapped_column(String(200), nullable=False)
    address_house_number: Mapped[str] = mapped_column(String(20), nullable=False)
    address_postal_code: Mapped[str] = mapped_column(String(4), nullable=False)
    address_locality: Mapped[str] = mapped_column(String(100), nullable=False)
    address_canton: Mapped[str] = mapped_column(String(2), nullable=False)
    address_country: Mapped[str] = mapped_column(String(2), nullable=False, default="CH")

    phone: Mapped[str] = mapped_column(String(20), nullable=False)

    # Encrypted at rest (EncryptedString) per Swiss addendum tax_id
    # requirement — see key-management caveat on Settings.tax_id_encryption_key.
    tax_id: Mapped[str] = mapped_column(EncryptedString(), nullable=False)

    # Self-FK for future dealer-group rollups (cross-cutting #1) — unused in
    # v1, no FK constraint added yet since Dealer groups aren't modeled.
    parent_group_id: Mapped[GUID | None] = mapped_column(GUID(), nullable=True)

    status: Mapped[DealerStatus] = mapped_column(
        SAEnum(DealerStatus, native_enum=False, length=32),
        nullable=False,
        default=DealerStatus.PENDING_ONBOARDING,
    )

    @property
    def address(self) -> dict[str, str]:
        """Read-side convenience: flat address_* columns as a nested dict
        matching app.schemas.dealer.DealerAddress. Writes go through the
        service layer, which unpacks the nested schema back into columns.
        """

        return {
            "street": self.address_street,
            "house_number": self.address_house_number,
            "postal_code": self.address_postal_code,
            "locality": self.address_locality,
            "canton": self.address_canton,
            "country": self.address_country,
        }
