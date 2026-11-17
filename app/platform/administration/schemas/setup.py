"""Setup checklist and activation (KAN-214). The legal documents' schemas
are in `legal.py`."""

import datetime as dt
import uuid

from pydantic import Field

from app.core.schemas import CamelModel
from app.platform.identity.schemas.legal import LegalAcceptanceDue
from app.platform.organisation.models.dealership import DealershipStatus


class SetupChecklistItemRead(CamelModel):
    code: str = Field(description="The item: branding, vat_and_rounding, bank_account, number_ranges, …")
    complete: bool
    blocks: str | None = Field(
        description="What an incomplete item refuses once the Betrieb is active; null = activation only."
    )
    link: str = Field(description="The SPA path that completes the item.")


class SetupChecklistRead(CamelModel):
    dealership_id: uuid.UUID
    status: DealershipStatus
    complete: bool
    items: list[SetupChecklistItemRead]
    # The legal step of activation: the current versions still to accept.
    legal_acceptance: LegalAcceptanceDue
    legal_documents_final: bool = Field(
        description="False in production while a current version is a draft: activation is refused."
    )
    may_activate: bool = Field(description="Every item complete, both documents accepted, and the texts final enough.")
    number_ranges_confirmed_at: dt.datetime | None
    version: int = Field(description="The dealership's version — If-Match for the activation.")


class ActivationRead(CamelModel):
    dealership_id: uuid.UUID
    status: DealershipStatus
    activated_at: dt.datetime | None
    version: int
