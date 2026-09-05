"""IntegrationProvider schemas (WP-6 PR-1)."""

import uuid

from pydantic import Field

from app.core.schemas import CamelModel


class ProviderCreate(CamelModel):
    provider_code: str
    category: str
    display_name: str
    auth_type: str
    required_secret_slots: list[str] = Field(default_factory=list)
    required_config_keys: list[str] = Field(default_factory=list)
    capability_codes: list[str] = Field(default_factory=list)
    docs_url: str | None = None
    supports_sandbox: bool = True


class ProviderUpdate(CamelModel):
    display_name: str | None = None
    docs_url: str | None = None
    capability_codes: list[str] | None = None
    required_config_keys: list[str] | None = None
    supports_sandbox: bool | None = None


class ProviderRead(CamelModel):
    id: uuid.UUID
    provider_code: str
    category: str
    display_name: str
    auth_type: str
    required_secret_slots: list[str]
    required_config_keys: list[str]
    capability_codes: list[str]
    docs_url: str | None
    supports_sandbox: bool
    version: int


class ProviderPage(CamelModel):
    items: list[ProviderRead]
