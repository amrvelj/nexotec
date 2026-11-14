"""Contact groups per Betrieb (KAN-466; Anto, 2026-10-26).

The groups themselves are the platform reference list `contact_group`
(`reference_value` rows, maintained by platform staff). What a Betrieb owns is
the switch: which of them it uses (`dealership_contact_group`, a row = on).
A module never reads that table — Customers asks
`platform.public.contact_group_codes` which codes the acting Betrieb may set.

A group switched off stays on the contacts that carry it; it is no longer
offered and can no longer be set there. A group platform staff deactivate on
the Reference data screen is treated as off everywhere, whatever the switch
says, and cannot be switched on.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import NotFoundError, UnprocessableEntityError
from app.core.export import ExportedTable, ExportScope, table_of
from app.platform.models.contact_group import CONTACT_GROUP_LIST_CODE, DealershipContactGroup
from app.platform.models.dealership import Dealership
from app.platform.models.reference_data import ReferenceList, ReferenceValue

# The initial set, Anto's ruling of 2026-10-26, as the seed migration
# (platform d72281c678c0) inserts it: (value_code, de, fr, it, en). The
# migration holds its own literals (ADR-133); tests/test_contact_groups.py
# keeps the two equal.
SEEDED_CONTACT_GROUPS: tuple[tuple[str, str, str, str, str], ...] = (
    ("private", "Privat", "Privé", "Privato", "Private"),
    ("commercial", "Gewerbe", "Professionnel", "Commerciale", "Commercial"),
    ("fleet", "Flotte", "Flotte", "Flotta", "Fleet"),
    ("dealer", "Händler", "Concessionnaire", "Concessionario", "Dealer"),
    ("importer", "Importeur", "Importateur", "Importatore", "Importer"),
    ("insurer", "Versicherung", "Assurance", "Assicurazione", "Insurer"),
    ("leasing", "Leasing", "Leasing", "Leasing", "Leasing"),
    (
        "public_authority",
        "Behörde / öffentliche Hand",
        "Autorité / secteur public",
        "Autorità / ente pubblico",
        "Public authority",
    ),
)

AUDIT_FIELD = "contact_groups"


@dataclass(frozen=True)
class ContactGroupOption:
    value: ReferenceValue
    enabled: bool


@dataclass(frozen=True)
class ContactGroupCodes:
    """What a Betrieb may do with contact groups. `settable` are the codes it
    has switched on that platform staff keep active; `known` every code of the
    platform list, active or not."""

    settable: frozenset[str]
    known: frozenset[str]


def _values(db: Session) -> list[ReferenceValue]:
    return list(
        db.scalars(
            select(ReferenceValue)
            .join(ReferenceList, ReferenceList.id == ReferenceValue.list_id)
            .where(ReferenceList.list_code == CONTACT_GROUP_LIST_CODE)
            .order_by(ReferenceValue.sort_order, ReferenceValue.value_code)
        ).all()
    )


def _enabled_codes(db: Session, dealership_id: uuid.UUID) -> set[str]:
    return set(
        db.scalars(
            select(DealershipContactGroup.value_code).where(DealershipContactGroup.dealership_id == dealership_id)
        ).all()
    )


def list_contact_groups(db: Session, dealership_id: uuid.UUID) -> list[ContactGroupOption]:
    """Every group of the platform list, in its order, with whether this
    Betrieb has it on. A deactivated value is listed (contacts may still carry
    it) and never reads as enabled."""

    enabled = _enabled_codes(db, dealership_id)
    return [
        ContactGroupOption(value=value, enabled=value.active and value.value_code in enabled) for value in _values(db)
    ]


def contact_group_codes(db: Session, dealership_id: uuid.UUID) -> ContactGroupCodes:
    values = _values(db)
    enabled = _enabled_codes(db, dealership_id)
    return ContactGroupCodes(
        settable=frozenset(v.value_code for v in values if v.active and v.value_code in enabled),
        known=frozenset(v.value_code for v in values),
    )


def set_contact_group_enabled(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    value_code: str,
    enabled: bool,
    actor_id: uuid.UUID,
    commit: bool = True,
) -> tuple[ContactGroupOption, bool]:
    """Switch one group on or off for the Betrieb. Idempotent: the same state
    again changes nothing and writes no audit row. Returns the group as it
    now stands and whether anything changed.

    The dealership row is locked FOR NO KEY UPDATE first, so two switches of
    the same Betrieb serialise and each audit row's before/after is the set
    it really changed (a child insert's FK check takes only KEY SHARE, which
    this lock does not block)."""

    db.scalars(select(Dealership.id).where(Dealership.id == dealership_id).with_for_update(key_share=True)).one()
    value = next((v for v in _values(db) if v.value_code == value_code), None)
    if value is None:
        raise NotFoundError(f"Contact group '{value_code}' was not found.")
    if enabled and not value.active:
        raise UnprocessableEntityError(
            f"Contact group '{value_code}' is no longer offered by Nexotec and cannot be switched on.",
            details={"reason": "contact_group_inactive", "valueCode": value_code},
        )
    before = _enabled_codes(db, dealership_id)
    changed = (value_code in before) != enabled
    if changed:
        if enabled:
            db.add(
                DealershipContactGroup(
                    dealership_id=dealership_id, value_code=value_code, created_by=actor_id, updated_by=actor_id
                )
            )
        else:
            row = db.scalars(
                select(DealershipContactGroup).where(
                    DealershipContactGroup.dealership_id == dealership_id,
                    DealershipContactGroup.value_code == value_code,
                )
            ).one()
            db.delete(row)
        after = (before | {value_code}) if enabled else (before - {value_code})
        db.flush()
        record_audit_event(
            db,
            entity_type="dealership",
            entity_id=dealership_id,
            tenant_id=dealership_id,
            action="update",
            actor_id=actor_id,
            before={AUDIT_FIELD: sorted(before)},
            after={AUDIT_FIELD: sorted(after)},
        )
    if commit:
        db.commit()
    return ContactGroupOption(value=value, enabled=enabled and value.active), changed


def export_table() -> ExportedTable:
    """KAN-218: the Betrieb's switched-on groups in its export, deleted with
    it after the retention period. Declared here, the switch's one module."""

    table = table_of(DealershipContactGroup)

    def scope(export_scope: ExportScope):
        return table.c.dealership_id == export_scope.dealership_id

    return ExportedTable(table=table, scope=scope)
