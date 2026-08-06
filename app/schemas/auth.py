from pydantic import EmailStr, Field

from app.schemas.base import CamelModel
from app.schemas.user import UserRead


class CredentialSetRequest(CamelModel):
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(CamelModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class LoginResponse(CamelModel):
    """The JWT itself never appears in this body — it's delivered only as
    the httpOnly session cookie (see app.core.auth.SESSION_COOKIE_NAME).
    This is what the frontend uses to render "logged in as X" state, since
    it can't read the cookie's claims directly.
    """

    user: UserRead


class LogoutResponse(CamelModel):
    ok: bool = True
