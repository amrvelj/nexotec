import datetime as dt
import uuid
from decimal import Decimal

from pydantic import Field, model_validator

from app.core.schemas import CamelModel
from app.core.validators import CantonCode, E164Phone, SwissPostalCode
from app.platform.models.dealership import DealershipStatus, FranchiseType
from app.platform.schemas.patch import refuse_null_on_not_null_fields


class DealershipAddress(CamelModel):
    street: str = Field(max_length=200)
    house_number: str = Field(max_length=20)
    postal_code: SwissPostalCode
    locality: str = Field(max_length=100)
    canton: CantonCode
    country: str = Field(default="CH", max_length=2)


class DealershipCreate(CamelModel):
    """dealer_group_id is optional: omitting it creates a new group of one
    for this dealership (the common case — onboarding a standalone dealer).
    Pass an existing group's id to add a second dealership to it instead.
    """

    dealer_group_id: uuid.UUID | None = None
    legal_name: str = Field(max_length=200, min_length=1)
    dba_name: str | None = Field(default=None, max_length=200)
    dealer_license_number: str = Field(max_length=64, min_length=1)
    license_state: CantonCode
    franchise_type: FranchiseType
    oem_affiliations: list[str] | None = None
    address: DealershipAddress
    phone: E164Phone
    tax_id: str = Field(min_length=1, description="Write-only; never returned by read endpoints.")


class DealershipUpdate(CamelModel):
    """All fields optional — PATCH semantics, partial update. `status` and
    `tax_id` changes are audit-logged by the service layer.
    """

    legal_name: str | None = Field(default=None, max_length=200, min_length=1)
    dba_name: str | None = Field(default=None, max_length=200)
    dealer_license_number: str | None = Field(default=None, max_length=64, min_length=1)
    license_state: CantonCode | None = None
    franchise_type: FranchiseType | None = None
    oem_affiliations: list[str] | None = None
    address: DealershipAddress | None = None
    phone: E164Phone | None = None
    tax_id: str | None = Field(default=None, min_length=1)
    status: DealershipStatus | None = None
    # WP-7 PR-3 (ADR-057) — "dealer-configurable dealer_settings.vat_rate,"
    # the one VAT figure in the whole system.
    vat_rate: Decimal | None = None
    # WP-7 PR-7 (FR-I-14) — a notification-only consumer, never the grid's
    # own fixed ageingBucket colour cue.
    ageing_alert_thresholds: list[int] | None = None

    @model_validator(mode="after")
    def _not_null_fields_are_not_cleared(self) -> "DealershipUpdate":
        # KAN-312: null clears only the four nullable columns.
        refuse_null_on_not_null_fields(
            self, nullable={"dba_name", "oem_affiliations", "vat_rate", "ageing_alert_thresholds"}
        )
        return self


class DealershipRead(CamelModel):
    id: uuid.UUID
    dealer_group_id: uuid.UUID
    legal_name: str
    dba_name: str | None
    dealer_license_number: str
    license_state: str
    franchise_type: FranchiseType
    oem_affiliations: list[str] | None
    address: DealershipAddress
    phone: str
    status: DealershipStatus
    vat_rate: Decimal | None
    ageing_alert_thresholds: list[int] | None
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


class DealershipPage(CamelModel):
    items: list[DealershipRead]
    next_cursor: str | None


class DealerGroupRead(CamelModel):
    id: uuid.UUID
    name: str
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    group_read_enabled: bool
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime
