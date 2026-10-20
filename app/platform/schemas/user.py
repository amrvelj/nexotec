import datetime as dt
import uuid

from pydantic import EmailStr, Field, model_validator

from app.core.auth import AccessRole
from app.core.schemas import CamelModel
from app.core.validators import E164Phone
from app.platform.models.user import EmploymentStatus, UserRole, UserStatus
from app.platform.schemas.patch import refuse_null_on_not_null_fields

# KAN-200: the PATCH fields that may be sent as null to clear them (nullable
# columns); every other field is NOT NULL and an explicit null is a 422.
_CLEARABLE = {
    "phone",
    "direct_dial",
    "employee_number",
    "department",
    "line_manager_user_id",
    "sales_advisor_code",
    "valid_from",
    "valid_to",
    "primary_location_id",
}


class UserCreate(CamelModel):
    """dealership_id is not part of the body — it comes from the
    POST /v1/dealerships/{id}/users path, per the spec's endpoint shape.

    access_roles/is_dealer_manager (WP-2 PR-2) replace the old scalar
    access_role — at least one functional role is required even for a
    manager, same as every other user (RP-3: "primary role grants a preset;
    the dealer manager additively enables further modules").

    KAN-200 (Users → Fields): the organisation, commercial and lifecycle
    fields the Users screen edits, every one optional — nothing newly
    required (a required field would be a breaking contract change).
    `phone` is the mobile number; `direct_dial` the desk extension.
    """

    first_name: str = Field(max_length=100, min_length=1)
    last_name: str = Field(max_length=100, min_length=1)
    email: EmailStr
    phone: E164Phone | None = None
    role: UserRole
    access_roles: list[AccessRole] = Field(min_length=1)
    is_dealer_manager: bool = False
    employment_status: EmploymentStatus = EmploymentStatus.ACTIVE
    # KAN-196: the subject the identity provider resolves the user by. A
    # local-password user needs none — it is generated (`local:<user id>`);
    # a federated one ([P3], D-A-11) carries the provider's subject.
    auth_identity_id: str | None = Field(default=None, max_length=255, min_length=1)
    direct_dial: E164Phone | None = None
    employee_number: str | None = Field(default=None, max_length=50, min_length=1)
    department: str | None = Field(default=None, max_length=100, min_length=1)
    line_manager_user_id: uuid.UUID | None = None
    sales_advisor_code: str | None = Field(default=None, max_length=50, min_length=1)
    commission_relevant: bool = False
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    primary_location_id: uuid.UUID | None = None
    additional_location_ids: list[uuid.UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validity_window_is_ordered(self) -> "UserCreate":
        _assert_window(self.valid_from, self.valid_to)
        return self


class UserUpdate(CamelModel):
    """PATCH semantics: a field left out is left unchanged. `None` on every
    field means "not sent"; only the nullable fields in `_CLEARABLE` may be
    sent as null (to clear them) — every other field is a NOT NULL column,
    so an explicit null is refused here as a 422 rather than reaching the
    service (KAN-136).

    `status` carries the lifecycle (Dealer Administration, Users →
    Lifecycle): suspend and reactivate here; deactivation has its own
    endpoint with the reassignment step (FR-A-37) and is refused here."""

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
    direct_dial: E164Phone | None = None
    employee_number: str | None = Field(default=None, max_length=50, min_length=1)
    department: str | None = Field(default=None, max_length=100, min_length=1)
    line_manager_user_id: uuid.UUID | None = None
    sales_advisor_code: str | None = Field(default=None, max_length=50, min_length=1)
    commission_relevant: bool | None = None
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    primary_location_id: uuid.UUID | None = None
    additional_location_ids: list[uuid.UUID] | None = None

    @model_validator(mode="after")
    def _not_null_fields_are_not_cleared(self) -> "UserUpdate":
        refuse_null_on_not_null_fields(self, nullable=_CLEARABLE)
        if "valid_from" in self.model_fields_set and "valid_to" in self.model_fields_set:
            _assert_window(self.valid_from, self.valid_to)
        return self


def _assert_window(valid_from: dt.date | None, valid_to: dt.date | None) -> None:
    if valid_from is not None and valid_to is not None and valid_to < valid_from:
        raise ValueError("validTo must not be before validFrom.")


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
    # KAN-200 (Users → Fields). Defaults keep every existing reader (the
    # login response, the membership routes) valid; the users routes fill
    # the derived fields below through `user_service.user_read`.
    direct_dial: str | None = None
    employee_number: str | None = None
    department: str | None = None
    line_manager_user_id: uuid.UUID | None = None
    sales_advisor_code: str | None = None
    commission_relevant: bool = False
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    invited_at: dt.datetime | None = None
    last_login_at: dt.datetime | None = None
    deactivated_at: dt.datetime | None = None
    deactivation_reason: str | None = None
    has_signature_image: bool = False
    # Derived (not columns): the live invitation's expiry for an `invited`
    # user — past it, the list shows "invitation expired" (FR-A-11) — and
    # the user's sites (`user_location`).
    invitation_expires_at: dt.datetime | None = None
    primary_location_id: uuid.UUID | None = None
    additional_location_ids: list[uuid.UUID] = Field(default_factory=list)


class SessionUserRead(CamelModel):
    """`LoginResponse.user` and `GET /auth/me` — exactly the fields they
    carried before KAN-200 (the ticket's do-not-touch). The administration
    fields and the derived ones (`hasSignatureImage`, the sites, the
    invitation expiry) are `UserRead`'s, read through `user_read`; a plain
    `model_validate` of the row cannot fill them, so the session never
    pretends to."""

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


class LocationRead(CamelModel):
    """A site of the Betrieb, for the Users screen's location picker
    (KAN-200). The Locations screen itself is Phase 2."""

    id: uuid.UUID
    name: str
    address_locality: str | None


class LocationList(CamelModel):
    items: list[LocationRead]


# --- Deactivation with the reassignment step (FR-A-37, D-A-35) ------------


class OwnedGroupRead(CamelModel):
    """One group of open records the user owns: the module, the group in
    it and the count. Labels are the SPA's (i18n by module + group)."""

    module: str
    group: str
    count: int


class OwnershipRead(CamelModel):
    """What the user still owns, across every registered module — the
    deactivation dialog's list. `modules` names every module that answered,
    so "nothing to reassign" is a statement, not an omission."""

    groups: list[OwnedGroupRead]
    modules: list[str]


class OwnershipTransferRequest(CamelModel):
    """The manager's decision for one group: a colleague (`to_user_id`), or
    null — the records go to the module's own unassigned queue."""

    module: str = Field(max_length=32, min_length=1)
    group: str = Field(max_length=32, min_length=1)
    to_user_id: uuid.UUID | None = None


class OwnershipTransfersRequest(CamelModel):
    """A hand-over outside the deactivation: the retry after a module failed,
    or a plain hand-over for a user who stays. `reason` lands on platform's
    audit row and on the modules' own (a customer's advisor change, say)."""

    transfers: list[OwnershipTransferRequest]
    reason: str | None = Field(default=None, max_length=500)


class OwnershipTransferResult(CamelModel):
    module: str
    group: str
    to_user_id: uuid.UUID | None
    count: int


class OwnershipTransfersRead(CamelModel):
    transfers: list[OwnershipTransferResult]


class UserDeactivateRequest(CamelModel):
    """`reason` is stored on the user and on the audit row; `transfers`
    must decide every group the user still owns (422 names the missing
    ones) — never silently with the leaver."""

    reason: str = Field(max_length=500, min_length=1)
    transfers: list[OwnershipTransferRequest] = Field(default_factory=list)
    # The employment ends with the access, or stays (a seasonal hire whose
    # access ends while the record says `on_leave`): the caller says which;
    # left out, the employment status is untouched.
    employment_status: EmploymentStatus | None = None


class UserDeactivatedRead(CamelModel):
    user: UserRead
    transfers: list[OwnershipTransferResult]
