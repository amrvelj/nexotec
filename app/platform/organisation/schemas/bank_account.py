"""Bank accounts of a Betrieb (KAN-203, D-A-27). The IBAN types validate the
ISO 7064 check on save and tell a QR-IBAN (IID 30000–31999) from a plain
one; which reference goes with which is the service's rule
(app.platform.organisation.services.bank_account.assert_qr_consistent), because a PATCH
only sees the fields it carries.
"""

import datetime as dt
import uuid

from pydantic import Field, model_validator

from app.core.schemas import CamelModel
from app.core.validators import PlainIban, QrIban
from app.platform.organisation.models.dealership_bank_account import QrReferenceType
from app.platform.reference.schemas.patch import refuse_null_on_not_null_fields

# ISO 9362 BIC: 8 or 11 characters, upper case.
_BIC_PATTERN = r"^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$"
# A structured creditor reference prefix (ISO 11649 body is up to 21 alphanumerics).
_CREDITOR_PREFIX_PATTERN = r"^[A-Za-z0-9]{1,21}$"


class BankAccountCreate(CamelModel):
    account_holder: str = Field(max_length=200, min_length=1)
    bank_name: str = Field(max_length=200, min_length=1)
    iban: PlainIban
    bic: str | None = Field(default=None, pattern=_BIC_PATTERN)
    qr_iban: QrIban | None = None
    qr_reference_type: QrReferenceType = QrReferenceType.NON
    creditor_reference_prefix: str | None = Field(default=None, pattern=_CREDITOR_PREFIX_PATTERN)
    label: str | None = Field(default=None, max_length=100)
    is_default: bool = False


class BankAccountUpdate(CamelModel):
    """PATCH semantics: a field not sent keeps its value; null clears a
    nullable one. `isDefault: true` makes this the invoice account and takes
    the flag off the previous default in the same transaction."""

    account_holder: str | None = Field(default=None, max_length=200, min_length=1)
    bank_name: str | None = Field(default=None, max_length=200, min_length=1)
    iban: PlainIban | None = None
    bic: str | None = Field(default=None, pattern=_BIC_PATTERN)
    qr_iban: QrIban | None = None
    qr_reference_type: QrReferenceType | None = None
    creditor_reference_prefix: str | None = Field(default=None, pattern=_CREDITOR_PREFIX_PATTERN)
    label: str | None = Field(default=None, max_length=100)
    is_default: bool | None = None

    @model_validator(mode="after")
    def _not_null_fields_are_not_cleared(self) -> "BankAccountUpdate":
        refuse_null_on_not_null_fields(self, nullable={"bic", "qr_iban", "creditor_reference_prefix", "label"})
        return self


class BankAccountRead(CamelModel):
    id: uuid.UUID
    dealership_id: uuid.UUID
    account_holder: str
    bank_name: str
    iban: str
    bic: str | None
    qr_iban: str | None
    qr_reference_type: QrReferenceType
    creditor_reference_prefix: str | None
    label: str | None
    is_default: bool
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


class BankAccountList(CamelModel):
    items: list[BankAccountRead]
