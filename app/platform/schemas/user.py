import datetime as dt
import uuid

from pydantic import EmailStr, Field, model_validator

from app.core.auth import AccessRole
from app.core.schemas import CamelModel
from app.core.validators import E164Phone
from app.platform.models.user import EmploymentStatus, UserRole, UserStatus
from app.platform.schemas.patch import refuse_null_on_not_null_fields


class UserCreate(CamelModel):
    """dealership_id is not part of the body — it comes from the
    POST /v1/dealerships/{id}/users path, per the spec's endpoint shape.

    access_roles/is_dealer_manager (WP-2 PR-2) replace the old scalar
    access_role — at least one functional role is required even for a
    manager, same as every other user (RP-3: "primary role grants a preset;
    the dealer manager additively enables further modules").
    """

    first_name: str = Field(max_length=100, min_length=1)
    last_name: str = Field(max_length=100, min_length=1)
    email: EmailStr
    phone: E164Phone | None = None
    role: UserRole
    access_roles: list[AccessRole] = Field(min_length=1)
    is_dealer_manager: bool = False
    employment_status: EmploymentStatus = EmploymentStatus.ACTIVE
    auth_identity_id: str = Field(max_length=255, min_length=1)


class UserUpdate(CamelModel):
    """PATCH semantics: a field left out is left unchanged. `None` on every
    field means "not sent"; only `phone` may be sent as null (to clear it) —
    every other field is a NOT NULL column, so an explicit null is refused
    here as a 422 rather than reaching the service (KAN-136)."""

    first_name: str | None = Field(default=None, max_length=100, min_length=1)
    last_name: str | None = Field(default=None, max_length=100, min_length=1)
    email: EmailStr | None = None
    phone: E164Phone | None = None
    role: UserRole | None = None
    access_roles: list[AccessRole] | None = Field(default=None, min_length=1)
    is_dealer_manager: bool | None = None
    employment_status: EmploymentStatus | None = None
    auth_identity_id: str | None = Field(default=None, max_length=255, min_length=1)
    status: UserStatus | None = None

    @model_validator(mode="after")
    def _not_null_fields_are_not_cleared(self) -> "UserUpdate":
        refuse_null_on_not_null_fields(self, nullable={"phone"})
        return self


class UserRead(CamelModel):
    id: uuid.UUID
    dealership_id: uuid.UUID
    first_name: str
    last_name: str
    email: str
    phone: str | None
    role: UserRole
    access_roles: list[AccessRole]
    is_dealer_manager: bool
    employment_status: EmploymentStatus
    auth_identity_id: str
    status: UserStatus
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


class UserPage(CamelModel):
    items: list[UserRead]
    next_cursor: str | None
