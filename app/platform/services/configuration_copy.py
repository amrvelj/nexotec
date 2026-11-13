"""Copy configuration from a sister Betrieb (KAN-220; Dealer Administration
PRD D-A-01, FR-A-01; Anto 2026-11-03, option 1).

The manager flag is held per dealership and never inherited (D-A-01). This is
the mitigation: a manager holding the flag in **both** Betriebe copies a
chosen subset of the source's configuration into the target — once. The
copies are independent: no foreign key, no sync flag, no "copied from" column
on any copied row. The only trace of the source is the one summary audit row
on the target (`configuration_copied`), which is history, not a link.

What the first version copies (option 1 — what exists today; each later
entity joins in its own ticket: labour rate categories with KAN-224, contact
groups with KAN-466; FR-A-01's "role presets" has nothing behind it, D-A-03):

- **standard texts** — the source's own texts and its adaptations of seeds,
  at Betrieb scope. An adaptation is copied as the target's own adaptation of
  the **seed** (`overridden_from_id` names the platform seed, never the
  source's row); a Betrieb-own text keeps its code, so a later copy finds and
  overwrites it instead of duplicating it. A text the target already has of
  the same code and language is overwritten (settled (a)); a copied default
  that takes a slot from the target's own default is reported
  (`default_replaced`); the source's reviewed seed version travels with an
  adaptation, so "the standard text has changed" survives; a location variant
  is not copied — sites differ per Betrieb (settled (b)).
- **the document template's header notes** (the four languages); a
  language the source has no note in leaves the target's note as it is.
- **number-range patterns** — pattern, start value and yearly reset, for the
  modules the target holds. Never `last_value` or `last_year`, never the
  freeze: a range of the target that has handed out a number is frozen and
  is skipped and reported, and a pattern the target cannot take (no location
  code for `{location_code}`, a counter too short for its next value) is
  skipped with the reason.

Every item goes through its entity's own write path (validation, audit row,
version), with the commit left to this service. The dry run runs the very
same path and rolls the transaction back, so it lists exactly what the commit
would create, overwrite or skip.
"""

import enum
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.auth import Principal
from app.core.errors import AppError, NotFoundError
from app.core.permissions import AccessRole, ModuleCode
from app.core.tenancy import require_tenant_match
from app.platform.models.dealership import Dealership
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.number_range import NumberRange, NumberRangeDocumentType
from app.platform.models.standard_text import StandardText, StandardTextSource
from app.platform.models.user import User
from app.platform.schemas.document_template import DocumentTemplateUpdate
from app.platform.services import document_template as document_template_service
from app.platform.services import number_range as number_range_service
from app.platform.services import standard_text as standard_text_service
from app.platform.services.entitlement import list_active_modules
from app.platform.services.user import is_dealer_manager_in
from app.platform.services.user_common import membership_is_active

SUMMARY_ACTION = "configuration_copied"
_HEADER_FIELDS = ("header_note_de", "header_note_fr", "header_note_it", "header_note_en")
_TEXT_FIELDS = ("title", "body", "is_default", "active", "sort_order", "template_approved", "provider_template_id")


class CopySubset(str, enum.Enum):
    STANDARD_TEXTS = "standard_texts"
    DOCUMENT_TEMPLATE = "document_template"
    NUMBER_RANGES = "number_ranges"


class CopyOutcome(str, enum.Enum):
    CREATED = "created"
    OVERWRITTEN = "overwritten"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"


@dataclass
class CopyItem:
    subset: CopySubset
    # What the item is, in the target's terms: a text's code and language, the
    # template, a range's document type. Never an id of the source's row.
    key: str
    outcome: CopyOutcome
    label: str | None = None
    language: str | None = None
    # Why an item was skipped: `number_range_frozen` or the 422 reason the
    # entity's own validation gave (`number_pattern_location_code_missing`, …).
    reason: str | None = None


@dataclass
class CopyReport:
    source_dealership_id: uuid.UUID
    source_legal_name: str
    dry_run: bool
    items: list[CopyItem] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {outcome.value: 0 for outcome in CopyOutcome}
        for item in self.items:
            out[item.outcome.value] += 1
        return out


@dataclass(frozen=True)
class SourceOption:
    id: uuid.UUID
    legal_name: str


def _not_found(dealership_id: uuid.UUID) -> NotFoundError:
    return NotFoundError(f"Dealership {dealership_id} was not found.")


def _manages(db: Session, principal: Principal, dealership_id: uuid.UUID) -> bool:
    if AccessRole.PLATFORM_ADMIN in principal.roles:
        return True
    user = db.get(User, principal.user_id)
    return user is not None and is_dealer_manager_in(db, user=user, dealership_id=dealership_id)


def require_target(db: Session, principal: Principal, target_id: uuid.UUID) -> Dealership:
    """The target is the active dealership (writes need the switch) and the
    caller holds its manager flag — else 404, never a hint (rule 7)."""

    require_tenant_match(target_id, principal)
    target = db.get(Dealership, target_id)
    # The session's own flag (minted for the active dealership) and the
    # database's: a session minted before the flag was taken away holds none.
    session_flag = AccessRole.PLATFORM_ADMIN in principal.roles or principal.is_dealer_manager
    if target is None or not session_flag or not _manages(db, principal, target_id):
        raise _not_found(target_id)
    return target


def require_source(db: Session, principal: Principal, *, target: Dealership, source_id: uuid.UUID) -> Dealership:
    """A sister Betrieb — any other Betrieb whose manager flag the caller
    holds too — else 404. Not limited to the target's dealer group: a
    membership may cross groups (ADR-106), and the flag on both sides is what
    FR-A-01 asks for; no authority crosses a legal entity that way."""

    source = db.get(Dealership, source_id) if source_id != target.id else None
    if source is None or not _manages(db, principal, source_id):
        raise _not_found(source_id)
    return source


def list_sources(db: Session, principal: Principal, *, target: Dealership) -> list[SourceOption]:
    """The sister Betriebe the caller may copy from: those where the caller
    holds the manager flag (home dealership or an active membership), the
    target left out. Platform staff hold no flag of their
    own and pick nothing here; the API still takes a source from them."""

    user = db.get(User, principal.user_id)
    if user is None:
        return []
    candidates = {user.tenant_id}
    candidates.update(
        db.scalars(
            select(DealershipMembership.dealership_id).where(
                DealershipMembership.user_id == user.id, membership_is_active()
            )
        ).all()
    )
    candidates.discard(target.id)
    options: list[SourceOption] = []
    for dealership in db.scalars(select(Dealership).where(Dealership.id.in_(candidates))).all():
        if not is_dealer_manager_in(db, user=user, dealership_id=dealership.id):
            continue
        options.append(SourceOption(id=dealership.id, legal_name=dealership.legal_name))
    return sorted(options, key=lambda option: option.legal_name.casefold())


# --- standard texts ------------------------------------------------------------------


def _dealer_rows(db: Session, dealership_id: uuid.UUID) -> list[StandardText]:
    return list(
        db.scalars(
            select(StandardText)
            .where(
                StandardText.dealership_id == dealership_id,
                StandardText.location_id.is_(None),
                StandardText.source == StandardTextSource.DEALER,
            )
            .order_by(StandardText.code, StandardText.language)
        ).all()
    )


def _text_input(row: StandardText) -> standard_text_service.DealerTextInput:
    return standard_text_service.DealerTextInput(
        code=row.code,
        language=row.language,
        title=row.title,
        body=row.body,
        applies_to=row.applies_to,
        slot=row.slot,
        location_id=None,
        is_default=row.is_default,
        active=row.active,
        sort_order=row.sort_order,
        template_approved=row.template_approved,
        provider_template_id=row.provider_template_id,
    )


def _copy_standard_texts(
    db: Session, *, source: Dealership, target: Dealership, actor_id: uuid.UUID, report: CopyReport
) -> None:
    target_rows = {(row.code, row.language): row for row in _dealer_rows(db, target.id)}
    target_codes = {code for code, _ in target_rows}
    for row in _dealer_rows(db, source.id):
        item = CopyItem(
            subset=CopySubset.STANDARD_TEXTS,
            key=row.code,
            label=row.title,
            language=row.language.value,
            outcome=CopyOutcome.CREATED,
        )
        existing = target_rows.get((row.code, row.language))
        # The overwritten row's own default flag is the overwrite, not a demotion.
        defaults_before = [r for r in target_rows.values() if r.is_default and r is not existing]
        try:
            with db.begin_nested():
                created = _write_text(
                    db,
                    row=row,
                    existing=existing,
                    target=target,
                    target_codes=target_codes,
                    actor_id=actor_id,
                    item=item,
                )
            if created is not None:
                target_rows[(created.code, created.language)] = created
                target_codes.add(created.code)
            # A copied default takes its slot: the target's own default there
            # stops printing. Said in the preview, never done silently.
            for demoted in defaults_before:
                if not demoted.is_default:
                    report.items.append(
                        CopyItem(
                            subset=CopySubset.STANDARD_TEXTS,
                            key=demoted.code,
                            label=demoted.title,
                            language=demoted.language.value,
                            outcome=CopyOutcome.OVERWRITTEN,
                            reason="default_replaced",
                        )
                    )
        except AppError as refused:
            # A seed the target cannot adapt (the language was withdrawn)
            # or a body the closed placeholder registry refuses: reported,
            # rolled back to the savepoint — never written half.
            item.outcome = CopyOutcome.SKIPPED
            item.reason = str((refused.details or {}).get("reason") or "refused")
        report.items.append(item)


def _write_text(
    db: Session,
    *,
    row: StandardText,
    existing: StandardText | None,
    target: Dealership,
    target_codes: set[str],
    actor_id: uuid.UUID,
    item: CopyItem,
) -> StandardText | None:
    """One text through its own write path; the created row, if any."""

    if existing is not None:
        fields = _TEXT_FIELDS + (("overridden_seed_version",) if row.overridden_from_id is not None else ())
        changes = {name: getattr(row, name) for name in fields if getattr(existing, name) != getattr(row, name)}
        if not changes:
            item.outcome = CopyOutcome.UNCHANGED
            return None
        standard_text_service.update_dealer_text(
            db, row=existing, changes=changes, if_match_version=existing.version, actor_id=actor_id, commit=False
        )
        item.outcome = CopyOutcome.OVERWRITTEN
        return None
    # An adaptation of a seed is created as the target's own adaptation (the
    # create path points it at the seed); a Betrieb-own text keeps its code —
    # new to the target unless another language of it was copied already.
    is_own = row.overridden_from_id is None
    return standard_text_service.create_dealer_text(
        db,
        dealership_id=target.id,
        category=row.category,
        data=_text_input(row),
        actor_id=actor_id,
        new_code=is_own and row.code not in target_codes,
        # The source's review state travels: an adaptation behind its seed
        # still reads "the standard text has changed" in the target.
        seed_version=row.overridden_seed_version,
        commit=False,
    )


# --- document template -----------------------------------------------------------------


def _copy_document_template(
    db: Session, *, source: Dealership, target: Dealership, actor_id: uuid.UUID, report: CopyReport
) -> None:
    source_template = document_template_service.get_document_template(db, source.id)
    target_template = document_template_service.get_document_template(db, target.id)
    current = {name: getattr(target_template, name) if target_template else None for name in _HEADER_FIELDS}
    # A language the source has no note in is not copied: an empty note never
    # clears the target's — the rule texts follow too (absent = kept).
    wanted = {
        name: value
        for name in _HEADER_FIELDS
        if (value := getattr(source_template, name) if source_template else None) not in (None, "")
    }
    item = CopyItem(subset=CopySubset.DOCUMENT_TEMPLATE, key="header_notes", outcome=CopyOutcome.UNCHANGED)
    changes = {name: value for name, value in wanted.items() if current[name] != value}
    if changes:
        document_template_service.upsert_document_template(
            db,
            dealership_id=target.id,
            data=DocumentTemplateUpdate(**changes),
            if_match_version=target_template.version if target_template else 0,
            actor_id=actor_id,
            commit=False,
        )
        item.outcome = CopyOutcome.CREATED if target_template is None else CopyOutcome.OVERWRITTEN
    report.items.append(item)


# --- number ranges ----------------------------------------------------------------------


def _target_modules(db: Session, target: Dealership) -> set[ModuleCode]:
    return {ModuleCode(value) for value in list_active_modules(db, target.id)}


def _copy_number_ranges(
    db: Session, *, source: Dealership, target: Dealership, actor_id: uuid.UUID, report: CopyReport
) -> None:
    held = _target_modules(db, target)
    source_rows = {
        row.document_type: row
        for row in db.scalars(select(NumberRange).where(NumberRange.tenant_id == source.id)).all()
    }
    target_rows = {
        row.document_type: row
        for row in db.scalars(select(NumberRange).where(NumberRange.tenant_id == target.id)).all()
    }
    for document_type in NumberRangeDocumentType:
        if number_range_service.MODULE_OF[document_type] not in held:
            continue
        # A source range never written reads as its seed — the same thing
        # the source's screen shows.
        wanted = source_rows.get(document_type) or number_range_service.new_range(source.id, document_type, None)
        item = CopyItem(
            subset=CopySubset.NUMBER_RANGES,
            key=document_type.value,
            label=wanted.pattern,
            outcome=CopyOutcome.UNCHANGED,
        )
        # Read first, write only for a change: a target range never written
        # reads as its seed and is created only if the copy changes it.
        row = target_rows.get(document_type) or number_range_service.new_range(target.id, document_type, None)
        if (row.pattern, row.start_value, row.yearly_reset) == (
            wanted.pattern,
            wanted.start_value,
            wanted.yearly_reset,
        ):
            report.items.append(item)
            continue
        if row.frozen_at is not None:
            item.outcome = CopyOutcome.SKIPPED
            item.reason = "number_range_frozen"
            report.items.append(item)
            continue
        row = number_range_service.get_or_seed(db, dealership_id=target.id, document_type=document_type, lock=True)
        if row.frozen_at is not None:
            item.outcome = CopyOutcome.SKIPPED
            item.reason = "number_range_frozen"
            report.items.append(item)
            continue
        try:
            with db.begin_nested():
                number_range_service.update_range(
                    db,
                    row=row,
                    pattern=wanted.pattern,
                    start_value=wanted.start_value,
                    yearly_reset=wanted.yearly_reset,
                    reason=None,
                    actor_id=actor_id,
                    actor_is_platform_admin=False,
                )
            item.outcome = CopyOutcome.OVERWRITTEN
        except AppError as refused:
            item.outcome = CopyOutcome.SKIPPED
            item.reason = str((refused.details or {}).get("reason") or "refused")
        report.items.append(item)


_COPIERS = {
    CopySubset.STANDARD_TEXTS: _copy_standard_texts,
    CopySubset.DOCUMENT_TEMPLATE: _copy_document_template,
    CopySubset.NUMBER_RANGES: _copy_number_ranges,
}


def copy_configuration(
    db: Session,
    principal: Principal,
    *,
    target_id: uuid.UUID,
    source_id: uuid.UUID,
    subsets: set[CopySubset],
    dry_run: bool,
) -> CopyReport:
    """The one-shot copy. One transaction: committed, or — for the dry run —
    rolled back after the very same writes, so the preview is the commit."""

    target = require_target(db, principal, target_id)
    source = require_source(db, principal, target=target, source_id=source_id)
    report = CopyReport(source_dealership_id=source.id, source_legal_name=source.legal_name, dry_run=dry_run)
    try:
        for subset in CopySubset:
            if subset in subsets:
                _COPIERS[subset](db, source=source, target=target, actor_id=principal.user_id, report=report)
        summary: dict[str, Any] = {
            "sourceDealershipId": str(source.id),
            "subsets": sorted(subset.value for subset in subsets),
            "counts": report.counts(),
        }
        record_audit_event(
            db,
            entity_type="dealership",
            entity_id=target.id,
            tenant_id=target.id,
            action=SUMMARY_ACTION,
            actor_id=principal.user_id,
            before=None,
            after=summary,
        )
        db.flush()
    except BaseException:
        db.rollback()
        raise
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return report
