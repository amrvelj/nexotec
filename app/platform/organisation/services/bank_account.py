"""The Betrieb's bank accounts (KAN-203, D-A-27): a child collection with at
most one default, every write audit-logged with before and after values
(A-RULE enforcement rule 7).

Rules the database does not hold:
- `qrr` needs a QR-IBAN and a QR-IBAN needs `qrr` — the QR-bill (WP-9) prints
  one or the other, never a QR-IBAN with a creditor reference;
- `creditorReferencePrefix` goes with `scor` only;
- the default is chosen explicitly (Anto, 2026-10-24): the first account is
  not promoted, `isDefault: true` moves the flag, and the default may be
  deleted or demoted — a Betrieb without a default has no invoice account yet.
Scoping: an account is looked up by (dealership, id); another Betrieb's id is
a 404 (rule 7, 404 never 403).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.platform.organisation.models.dealership_bank_account import DealershipBankAccount, QrReferenceType
from app.platform.organisation.schemas.bank_account import BankAccountCreate, BankAccountUpdate
from app.platform.organisation.services.dealership import get_dealership_for_update_or_404

_ENTITY = "dealership_bank_account"
_FIELDS = (
    "account_holder",
    "bank_name",
    "iban",
    "bic",
    "qr_iban",
    "qr_reference_type",
    "creditor_reference_prefix",
    "label",
    "is_default",
)


def _snapshot(account: DealershipBankAccount) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in _FIELDS:
        value = getattr(account, field)
        out[field] = value.value if hasattr(value, "value") else value
    return out


def assert_qr_consistent(
    *, qr_iban: str | None, qr_reference_type: QrReferenceType, creditor_reference_prefix: str | None
) -> None:
    if qr_reference_type is QrReferenceType.QRR and qr_iban is None:
        raise UnprocessableEntityError(
            "A QR reference (qrr) needs a QR-IBAN — enter the QR-IBAN or choose another reference type.",
            details={"reason": "qr_reference_needs_qr_iban"},
        )
    if qr_iban is not None and qr_reference_type is not QrReferenceType.QRR:
        raise UnprocessableEntityError(
            "A QR-IBAN is only used with the QR reference (qrr) — remove the QR-IBAN or set qrReferenceType to qrr.",
            details={"reason": "qr_iban_needs_qr_reference"},
        )
    if creditor_reference_prefix is not None and qr_reference_type is not QrReferenceType.SCOR:
        raise UnprocessableEntityError(
            "A creditor reference prefix applies to the structured creditor reference (scor) only.",
            details={"reason": "creditor_prefix_needs_scor"},
        )


def list_accounts(db: Session, *, dealership_id: uuid.UUID) -> list[DealershipBankAccount]:
    stmt = (
        select(DealershipBankAccount)
        .where(DealershipBankAccount.dealership_id == dealership_id)
        .order_by(DealershipBankAccount.is_default.desc(), DealershipBankAccount.created_at, DealershipBankAccount.id)
    )
    return list(db.scalars(stmt).all())


def get_account_or_404(
    db: Session, *, dealership_id: uuid.UUID, account_id: uuid.UUID, for_update: bool = False
) -> DealershipBankAccount:
    stmt = select(DealershipBankAccount).where(
        DealershipBankAccount.dealership_id == dealership_id, DealershipBankAccount.id == account_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    account = db.scalar(stmt)
    if account is None:
        raise NotFoundError(f"Bank account {account_id} was not found.")
    return account


def _take_default_from_others(db: Session, *, dealership_id: uuid.UUID, keep: uuid.UUID | None, actor_id: uuid.UUID) -> None:
    """Clears `is_default` on every other account of the dealership, under
    the dealership row lock the caller holds, and flushes so the partial
    unique index never sees two defaults at once."""

    stmt = select(DealershipBankAccount).where(
        DealershipBankAccount.dealership_id == dealership_id, DealershipBankAccount.is_default.is_(True)
    )
    if keep is not None:
        stmt = stmt.where(DealershipBankAccount.id != keep)
    others = db.scalars(stmt.with_for_update()).all()
    for other in others:
        before = _snapshot(other)
        other.is_default = False
        other.updated_by = actor_id
        other.version += 1
        record_audit_event(
            db,
            entity_type=_ENTITY,
            entity_id=other.id,
            tenant_id=dealership_id,
            action="update",
            actor_id=actor_id,
            before={"is_default": before["is_default"]},
            after={"is_default": False},
            reason="default moved to another account",
        )
    db.flush()


def create_account(
    db: Session, *, dealership_id: uuid.UUID, data: BankAccountCreate, actor_id: uuid.UUID
) -> DealershipBankAccount:
    assert_qr_consistent(
        qr_iban=data.qr_iban,
        qr_reference_type=data.qr_reference_type,
        creditor_reference_prefix=data.creditor_reference_prefix,
    )
    get_dealership_for_update_or_404(db, dealership_id)  # serialises default changes per Betrieb
    if data.is_default:
        _take_default_from_others(db, dealership_id=dealership_id, keep=None, actor_id=actor_id)
    account = DealershipBankAccount(
        dealership_id=dealership_id,
        account_holder=data.account_holder,
        bank_name=data.bank_name,
        iban=data.iban,
        bic=data.bic,
        qr_iban=data.qr_iban,
        qr_reference_type=data.qr_reference_type,
        creditor_reference_prefix=data.creditor_reference_prefix,
        label=data.label,
        is_default=data.is_default,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(account)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Another account became the default in the meantime — reload and try again.",
            details={"reason": "default_conflict"},
        ) from exc
    record_audit_event(
        db,
        entity_type=_ENTITY,
        entity_id=account.id,
        tenant_id=dealership_id,
        action="create",
        actor_id=actor_id,
        after=_snapshot(account),
    )
    db.commit()
    db.refresh(account)
    return account


def update_account(
    db: Session, *, account: DealershipBankAccount, data: BankAccountUpdate, actor_id: uuid.UUID
) -> DealershipBankAccount:
    changes = data.model_dump(exclude_unset=True)
    merged = {**_snapshot(account), **{k: (v.value if hasattr(v, "value") else v) for k, v in changes.items()}}
    assert_qr_consistent(
        qr_iban=merged["qr_iban"],
        qr_reference_type=QrReferenceType(merged["qr_reference_type"]),
        creditor_reference_prefix=merged["creditor_reference_prefix"],
    )
    before = _snapshot(account)
    if changes.get("is_default") is True and not account.is_default:
        get_dealership_for_update_or_404(db, account.dealership_id)
        _take_default_from_others(db, dealership_id=account.dealership_id, keep=account.id, actor_id=actor_id)
    for field, value in changes.items():
        setattr(account, field, value)
    account.updated_by = actor_id
    account.version += 1
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Another account became the default in the meantime — reload and try again.",
            details={"reason": "default_conflict"},
        ) from exc
    after = _snapshot(account)
    diff_before = {k: v for k, v in before.items() if after[k] != v}
    diff_after = {k: v for k, v in after.items() if before[k] != v}
    if diff_before or diff_after:
        record_audit_event(
            db,
            entity_type=_ENTITY,
            entity_id=account.id,
            tenant_id=account.dealership_id,
            action="update",
            actor_id=actor_id,
            before=diff_before or None,
            after=diff_after or None,
        )
    db.commit()
    db.refresh(account)
    return account


def delete_account(db: Session, *, account: DealershipBankAccount, actor_id: uuid.UUID) -> None:
    # KAN-204: a location's bank-account override names this row; deleting it
    # would silently send that site's invoices to the default account.
    from app.platform.organisation.services.location import location_ids_using_bank_account

    if location_ids_using_bank_account(db, account_id=account.id):
        raise ConflictError(
            "A location uses this account as its override — remove the override on the location first.",
            details={"reason": "bank_account_used_by_location"},
        )
    before = _snapshot(account)
    record_audit_event(
        db,
        entity_type=_ENTITY,
        entity_id=account.id,
        tenant_id=account.dealership_id,
        action="delete",
        actor_id=actor_id,
        before=before,
    )
    db.delete(account)
    db.commit()
