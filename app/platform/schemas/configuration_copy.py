"""Copy configuration from a sister Betrieb (KAN-220; FR-A-01)."""

import uuid

from pydantic import Field

from app.core.schemas import CamelModel
from app.platform.services.configuration_copy import CopyOutcome, CopySubset


class ConfigurationCopyRequest(CamelModel):
    source_dealership_id: uuid.UUID
    # At least one subset; the screen ticks all three by default.
    subsets: list[CopySubset] = Field(min_length=1)
    dry_run: bool = True


class ConfigurationCopyItem(CamelModel):
    subset: CopySubset
    key: str
    outcome: CopyOutcome
    label: str | None = None
    language: str | None = None
    reason: str | None = None


class ConfigurationCopyCounts(CamelModel):
    created: int
    overwritten: int
    unchanged: int
    skipped: int


class ConfigurationCopyResult(CamelModel):
    source_dealership_id: uuid.UUID
    source_legal_name: str
    dry_run: bool
    items: list[ConfigurationCopyItem]
    counts: ConfigurationCopyCounts


class ConfigurationCopySource(CamelModel):
    id: uuid.UUID
    legal_name: str


class ConfigurationCopySourceList(CamelModel):
    items: list[ConfigurationCopySource]
