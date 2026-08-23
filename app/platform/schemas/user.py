import datetime as dt
import uuid

from pydantic import EmailStr, Field

from app.core.auth import AccessRole
from app.core.schemas import CamelModel
from app.core.validators import E164Phone
from app.platform.models.user import EmploymentStatus, UserRole, UserStatus


class UserCreate(CamelModel):
    """dealer_id is not part of the body — it comes from the
    POST /v1/dealers/{id}/users path, per the spec's endpoint shape.
    """

    first_name: str = Field(max_length=100, min_length=1)
    last_name: str = Field(max_length=100, min_length=1)
    email: EmailStr
    phone: E164Phone | None = None
    role: UserRole
    access_role: AccessRole
    employment_status: EmploymentStatus = EmploymentStatus.ACTIVE
    auth_identity_id: str = Field(max_length=255, min_length=1)


class UserUpdate(CamelModel):
    first_name: str | None = Field(default=None, max_length=100, min_length=1)
    last_name: str | None = Field(default=None, max_length=100, min_length=1)
    email: EmailStr | None = None
    phone: E164Phone | None = None
    role: UserRole | None = None
    access_role: AccessRole | None = None
    employment_status: EmploymentStatus | None = None
    auth_identity_id: str | None = Field(default=None, max_length=255, min_length=1)
    status: UserStatus | None = None


class UserRead(CamelModel):
    id: uuid.UUID
    dealer_id: uuid.UUID
    first_name: str
    last_name: str
    email: str
    phone: str | None
    role: UserRole
    access_role: AccessRole
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
