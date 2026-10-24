"""dealership_bank_account (KAN-203, D-A-27): the Betrieb's bank accounts as a
child collection — a garage commonly runs a second account for the workshop
or for vehicle purchasing, so this is a table, never four columns on
`dealership`.

At most one row per dealership is the default (the partial unique index
`uq_dealership_bank_account_default`); the invoice footer and the QR-bill
(WP-9) read that one. The default is chosen explicitly (Anto, 2026-10-24):
the first account is not promoted on its own, and a Betrieb without a
default has no invoice account yet — the activation checklist's business,
not this table's.

`iban` is a plain IBAN (ISO 13616, mod-97 checked on save); `qr_iban` is a
QR-IBAN, told apart by its IID 30000..31999 (SIX, QR-bill IG) — the two are
never interchangeable, and `qr_reference_type` says which reference the
QR-bill carries: `qrr` needs the QR-IBAN, `scor` (ISO 11649 creditor
reference) and `non` go with the plain IBAN. `creditor_reference_prefix` is
the structured-reference prefix a camt.054 is matched on.

Nothing here is a secret (ADR-037: Nexotec operates no payment instrument;
an IBAN is a destination), but the matrix restricts who reads it — manager,
finance, auditor — because an IBAN in a screenshot is a fraud vector. Both
ends are platform-owned, so the FK to dealership is real.
"""

import enum
import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID
from app.db import Base


class QrReferenceType(str, enum.Enum):
    QRR = "qrr"  # QR reference, 27 digits — needs a QR-IBAN
    SCOR = "scor"  # ISO 11649 creditor reference — plain IBAN
    NON = "non"  # no reference — plain IBAN


class DealershipBankAccount(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "dealership_bank_account"
    __table_args__ = (
        Index(
            "uq_dealership_bank_account_default",
            "dealership_id",
            unique=True,
            postgresql_where=text("is_default"),
            sqlite_where=text("is_default"),
        ),
    )

    dealership_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("dealership.id"), nullable=False, index=True
    )
    account_holder: Mapped[str] = mapped_column(String(200), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(200), nullable=False)
    iban: Mapped[str] = mapped_column(String(34), nullable=False)
    bic: Mapped[str | None] = mapped_column(String(11), nullable=True)
    qr_iban: Mapped[str | None] = mapped_column(String(34), nullable=True)
    qr_reference_type: Mapped[QrReferenceType] = mapped_column(
        StoredEnum(QrReferenceType, length=8), nullable=False, default=QrReferenceType.NON
    )
    creditor_reference_prefix: Mapped[str | None] = mapped_column(String(21), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # What the account is for, in the dealer's words ("Werkstatt", "Fahrzeugeinkauf").
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
