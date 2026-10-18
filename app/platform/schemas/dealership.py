import datetime as dt
import uuid
from decimal import Decimal

from pydantic import Field, model_validator

from app.core.schemas import CamelModel
from app.core.validators import (
    CantonCode,
    E164Phone,
    SwissUid,
    SwissVatNumber,
    validate_postal_code_for_country,
)
from app.platform.models.dealership import DealershipStatus, FranchiseType
from app.platform.schemas.patch import refuse_null_on_not_null_fields


class DealershipAddress(CamelModel):
    """The registered seat (D-A-25). The postal code is validated for the
    country — four digits for CH, a plausible code of up to 12 characters
    elsewhere (the same rule the customer address applies, D-11). No minimum
    lengths: the model is also the read shape, and a row the old API
    accepted must still read."""

    street: str = Field(max_length=200)
    house_number: str = Field(max_length=20)
    postal_code: str = Field(max_length=12)
    locality: str = Field(max_length=100)
    canton: CantonCode
    country: str = Field(default="CH", max_length=2)

    @model_validator(mode="after")
    def _postal_code_fits_country(self) -> "DealershipAddress":
        self.country = self.country.upper()
        self.postal_code = validate_postal_code_for_country(self.postal_code, self.country)
        return self


class DealershipOverrideAddress(CamelModel):
    """A correspondence or billing override (D-A-25). Unlike the seat it may
    lie abroad, so the canton is required for a Swiss address and must be
    absent for any other country."""

    street: str = Field(max_length=200)
    house_number: str = Field(max_length=20)
    postal_code: str = Field(max_length=12)
    locality: str = Field(max_length=100)
    canton: CantonCode | None = None
    country: str = Field(default="CH", max_length=2)

    @model_validator(mode="after")
    def _canton_and_postal_code_fit_country(self) -> "DealershipOverrideAddress":
        self.country = self.country.upper()
        if self.country == "CH" and self.canton is None:
            raise ValueError("canton is required for a Swiss address.")
        if self.country != "CH" and self.canton is not None:
            raise ValueError("canton applies to Swiss addresses only — leave it out for another country.")
        self.postal_code = validate_postal_code_for_country(self.postal_code, self.country)
        return self


class DealershipCreate(CamelModel):
    """dealer_group_id is optional: omitting it creates a new group of one
    for this dealership (the common case — onboarding a standalone dealer).
    Pass an existing group's id to add a second dealership to it instead.

    KAN-197 (D-A-22, D-A-25 — a breaking contract change, decided up front):
    `uid` replaces the write-only `taxId`; `dealerLicenseNumber` and
    `licenseState` are gone; `dbaName` is `tradeName`; `vatNumber`,
    `vatLiable`, `correspondenceAddress` and `billingAddress` are new.
    """

    dealer_group_id: uuid.UUID | None = None
    legal_name: str = Field(max_length=200, min_length=1)
    trade_name: str | None = Field(default=None, max_length=200)
    franchise_type: FranchiseType
    oem_affiliations: list[str] | None = None
    address: DealershipAddress
    correspondence_address: DealershipOverrideAddress | None = None
    billing_address: DealershipOverrideAddress | None = None
    phone: E164Phone
    uid: SwissUid = Field(description="Swiss UID, CHE-123.456.789; public, unique per dealership.")
    vat_number: SwissVatNumber | None = Field(default=None, description="CHE-123.456.789 MWST (or TVA / IVA)")
    vat_liable: bool = False


class DealershipUpdate(CamelModel):
    """All fields optional — PATCH semantics, partial update. `status`,
    `legal_name`, `uid`, `vat_number` and `vat_liable` changes are
    audit-logged by the service layer.
    """

    legal_name: str | None = Field(default=None, max_length=200, min_length=1)
    trade_name: str | None = Field(default=None, max_length=200)
    franchise_type: FranchiseType | None = None
    oem_affiliations: list[str] | None = None
    address: DealershipAddress | None = None
    correspondence_address: DealershipOverrideAddress | None = None
    billing_address: DealershipOverrideAddress | None = None
    phone: E164Phone | None = None
    uid: SwissUid | None = None
    vat_number: SwissVatNumber | None = None
    vat_liable: bool | None = None
    status: DealershipStatus | None = None
    # WP-7 PR-3 (ADR-057) — "dealer-configurable dealer_settings.vat_rate,"
    # the one VAT figure in the whole system.
    vat_rate: Decimal | None = None
    # WP-7 PR-7 (FR-I-14) — a notification-only consumer, never the grid's
    # own fixed ageingBucket colour cue.
    ageing_alert_thresholds: list[int] | None = None

    @model_validator(mode="after")
    def _not_null_fields_are_not_cleared(self) -> "DealershipUpdate":
        # KAN-312: null clears only the nullable columns. `uid` is nullable
        # in the table for the migration's sake only (KAN-197) — the API
        # never clears it.
        refuse_null_on_not_null_fields(
            self,
            nullable={
                "trade_name",
                "oem_affiliations",
                "correspondence_address",
                "billing_address",
                "vat_number",
                "vat_rate",
                "ageing_alert_thresholds",
            },
        )
        return self


class DealershipRead(CamelModel):
    id: uuid.UUID
    dealer_group_id: uuid.UUID
    legal_name: str
    trade_name: str | None
    franchise_type: FranchiseType
    oem_affiliations: list[str] | None
    address: DealershipAddress
    correspondence_address: DealershipOverrideAddress | None
    billing_address: DealershipOverrideAddress | None
    phone: str
    # None only on a row KAN-197's migration could not convert (reported).
    uid: str | None
    vat_number: str | None
    vat_liable: bool
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
