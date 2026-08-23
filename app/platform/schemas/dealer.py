import datetime as dt
import uuid

from pydantic import Field

from app.core.schemas import CamelModel
from app.core.validators import CantonCode, E164Phone, SwissPostalCode
from app.platform.models.dealer import DealerStatus, FranchiseType


class DealerAddress(CamelModel):
    street: str = Field(max_length=200)
    house_number: str = Field(max_length=20)
    postal_code: SwissPostalCode
    locality: str = Field(max_length=100)
    canton: CantonCode
    country: str = Field(default="CH", max_length=2)


class DealerCreate(CamelModel):
    legal_name: str = Field(max_length=200, min_length=1)
    dba_name: str | None = Field(default=None, max_length=200)
    dealer_license_number: str = Field(max_length=64, min_length=1)
    license_state: CantonCode
    franchise_type: FranchiseType
    oem_affiliations: list[str] | None = None
    address: DealerAddress
    phone: E164Phone
    tax_id: str = Field(min_length=1, description="Write-only; never returned by read endpoints.")


class DealerUpdate(CamelModel):
    """All fields optional — PATCH semantics, partial update. `status` and
    `tax_id` changes are audit-logged by the service layer.
    """

    legal_name: str | None = Field(default=None, max_length=200, min_length=1)
    dba_name: str | None = Field(default=None, max_length=200)
    dealer_license_number: str | None = Field(default=None, max_length=64, min_length=1)
    license_state: CantonCode | None = None
    franchise_type: FranchiseType | None = None
    oem_affiliations: list[str] | None = None
    address: DealerAddress | None = None
    phone: E164Phone | None = None
    tax_id: str | None = Field(default=None, min_length=1)
    status: DealerStatus | None = None


class DealerRead(CamelModel):
    id: uuid.UUID
    legal_name: str
    dba_name: str | None
    dealer_license_number: str
    license_state: str
    franchise_type: FranchiseType
    oem_affiliations: list[str] | None
    address: DealerAddress
    phone: str
    parent_group_id: uuid.UUID | None
    status: DealerStatus
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


class DealerPage(CamelModel):
    items: list[DealerRead]
    next_cursor: str | None
