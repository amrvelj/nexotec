"""Onboarding a Betrieb (KAN-210; Dealer Administration D-A-06, FR-A-24/25)."""

import uuid

from pydantic import EmailStr, Field, model_validator

from app.core.auth import AccessRole
from app.core.permissions import ModuleCode
from app.core.schemas import CamelModel
from app.core.validators import E164Phone, SwissUid
from app.platform.identity.models.user import UserRole
from app.platform.organisation.models.dealership import FranchiseType
from app.platform.organisation.schemas.dealership import DealershipAddress


class OnboardingGroup(CamelModel):
    """Exactly one: an existing group, or a new one by name. A standalone
    Betrieb is a new group of one."""

    dealer_group_id: uuid.UUID | None = None
    new_group_name: str | None = Field(default=None, max_length=200, min_length=1)

    @model_validator(mode="after")
    def _exactly_one(self) -> "OnboardingGroup":
        if (self.dealer_group_id is None) == (self.new_group_name is None):
            raise ValueError("Name either an existing dealer group (dealerGroupId) or a new one (newGroupName).")
        return self


class OnboardingLegalEntity(CamelModel):
    legal_name: str = Field(max_length=200, min_length=1)
    trade_name: str | None = Field(default=None, max_length=200)
    franchise_type: FranchiseType
    legal_form: str | None = Field(default=None, max_length=50, description="A `legal_form` reference code.")
    uid: SwissUid = Field(description="Swiss UID, CHE-123.456.789; unique across Nexotec.")
    address: DealershipAddress = Field(description="The registered seat; it becomes the first location.")
    phone: E164Phone


class OnboardingManager(CamelModel):
    first_name: str = Field(max_length=100, min_length=1)
    last_name: str = Field(max_length=100, min_length=1)
    email: EmailStr
    role: UserRole = Field(description="The primary role (job title).")
    access_roles: list[AccessRole] = Field(min_length=1)


class OnboardingRequest(CamelModel):
    group: OnboardingGroup
    legal_entity: OnboardingLegalEntity
    # D-A-04: what Nexotec licenses; an empty list (no `sales`) is a valid
    # service-only garage (D-A-10).
    entitlements: list[ModuleCode]
    manager: OnboardingManager


class OnboardingResult(CamelModel):
    dealership_id: uuid.UUID
    dealer_group_id: uuid.UUID
    status: str
    entitlements: list[str]
    manager_user_id: uuid.UUID
    manager_email: str
    manager_status: str
    # Whether the platform sender took the invitation mail; when not, the
    # failure is a `mail_delivery_failed` auth event and staff re-issue it.
    invitation_delivered: bool
    location_id: uuid.UUID


class DealerGroupOption(CamelModel):
    id: uuid.UUID
    name: str


class DealerGroupOptionList(CamelModel):
    """One page of groups, by name (KAN-652): the shared page shape."""

    items: list[DealerGroupOption]
    next_cursor: str | None
