"""Standard texts (KAN-208; Dealer Administration PRD "Standard texts",
A-RULE-4, FR-A-07/08/28/29/54, D-A-23).

**Resolution** (PRD "Language fallback") is two chains, evaluated
independently and deterministically:

- language: the requested language → the Betrieb's
  `default_correspondence_language` → DE (duplicates dropped);
- scope: location → dealership → platform seed.

The language chain is the outer loop: the right language beats the narrower
scope, so a French customer gets the French seed before the Betrieb's German
wording. Within one language, for each code: an active location row wins; a
location row switched off falls back to the dealership's; a dealership row —
active or switched off — masks the seed of the same code (switching an
override off is how a Betrieb stops a seeded text). A text resolved by
*slot* is that language's default: location default, then dealership
default, then the seed's, ties by `sort_order` then `code`. A slot with no
default in any language of the chain resolves to nothing — the module prints
no text there; it never guesses one.

**Writes** are the Betrieb's dealer rows only. A dealer edit of a seed
creates an override row pointing at it (`overridden_from_id`, with the seed
version it was taken from); the seed itself is written only by
`platform_admin` through `update_platform_seed` (FR-A-29), which never
touches an override and whose version bump is what reads as "the standard
text has changed, review yours" on every override of it.

Every write is audited with before/after, flushes and commits here (the
caller owns no transaction around it), and is gated by the route's
`require_write_or_delegated("standard_texts")` on the path's `{category}`:
every lookup and every create binds to that category, so a delegate of one
category cannot write another's text by id.
"""

import datetime as dt
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.concurrency import check_version
from app.core.errors import NotFoundError, UnprocessableEntityError
from app.core.i18n import SwissLanguage
from app.core.uuid7 import uuid7
from app.platform.models.dealership import Dealership, Location
from app.platform.models.standard_text import (
    StandardText,
    StandardTextAppliesTo,
    StandardTextScope,
    StandardTextSlot,
    StandardTextSource,
)
from app.platform.models.standard_text_delegation import StandardTextCategory
from app.platform.services.dealership import get_dealership_or_404
from app.platform.services.standard_text_placeholders import (
    Placeholder,
    TextTarget,
    render_body,
    sample_values,
    target_for,
    validate_body,
)
from app.platform.services.standard_text_seed import SEED_TEXTS

FALLBACK_LANGUAGE = SwissLanguage.DE

_AUDITED_FIELDS = (
    "code",
    "category",
    "applies_to",
    "slot",
    "scope",
    "location_id",
    "language",
    "title",
    "body",
    "is_default",
    "active",
    "sort_order",
    "template_approved",
    "provider_template_id",
    "overridden_from_id",
    "overridden_seed_version",
)


def _refuse(reason: str, message: str, **details: Any) -> UnprocessableEntityError:
    return UnprocessableEntityError(message, details={"reason": reason, **details})


def language_chain(requested: SwissLanguage, dealership_default: SwissLanguage | None) -> list[SwissLanguage]:
    chain = [requested]
    for language in (dealership_default, FALLBACK_LANGUAGE):
        if language is not None and language not in chain:
            chain.append(language)
    return chain


def require_target(category: StandardTextCategory, applies_to: StandardTextAppliesTo) -> TextTarget:
    target = target_for(category, applies_to)
    if target is None:
        raise _refuse(
            "standard_text_target_unknown",
            f"No {category.value} text exists for {applies_to.value}.",
            category=category.value,
            appliesTo=applies_to.value,
        )
    return target


def _require_slot(target: TextTarget, slot: StandardTextSlot) -> None:
    if slot not in target.slots:
        raise _refuse(
            "standard_text_slot_unknown",
            f"{target.applies_to.value} has no {slot.value} slot for {target.category.value} texts.",
            slot=slot.value,
        )


def _dealership_name(dealership: Dealership) -> str:
    return dealership.trade_name or dealership.legal_name


def platform_values(dealership: Dealership, location: Location | None) -> dict[str, str | None]:
    """The two tokens platform resolves from its own records (see
    standard_text_placeholders.PLATFORM_RESOLVED). `location` carries no
    phone column yet, so `location.phone` is the Betrieb's phone either way."""

    return {
        Placeholder.DEALERSHIP_NAME.value: _dealership_name(dealership),
        Placeholder.LOCATION_PHONE.value: dealership.phone,
    }


def _location_of(db: Session, dealership_id: uuid.UUID, location_id: uuid.UUID | None) -> Location | None:
    """A location of this Betrieb, else 404 — a location id never reaches
    another tenant's site."""

    if location_id is None:
        return None
    location = db.get(Location, location_id)
    if location is None or location.tenant_id != dealership_id:
        raise NotFoundError(f"Location {location_id} was not found.")
    return location


# --- resolution ------------------------------------------------------------------


_SCOPE_RANK = {"location": 0, "dealership": 1, "seed": 2}


def _rank(row: StandardText) -> int:
    if row.dealership_id is None:
        return _SCOPE_RANK["seed"]
    return _SCOPE_RANK["location" if row.location_id is not None else "dealership"]


def _effective_rows(rows: Iterable[StandardText]) -> list[StandardText]:
    """One language's rows → the row each code resolves to (or none)."""

    by_code: dict[str, dict[int, StandardText]] = {}
    for row in rows:
        by_code.setdefault(row.code, {})[_rank(row)] = row
    effective: list[StandardText] = []
    for code in sorted(by_code):
        levels = by_code[code]
        location = levels.get(_SCOPE_RANK["location"])
        dealership = levels.get(_SCOPE_RANK["dealership"])
        seed = levels.get(_SCOPE_RANK["seed"])
        if location is not None and location.active:
            effective.append(location)
        elif dealership is not None:
            if dealership.active:
                effective.append(dealership)
        elif seed is not None and seed.active:
            effective.append(seed)
    return effective


def resolve_text(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    category: StandardTextCategory,
    applies_to: StandardTextAppliesTo,
    slot: StandardTextSlot,
    language: SwissLanguage,
    location_id: uuid.UUID | None = None,
    code: str | None = None,
) -> StandardText | None:
    """The row a module prints for (category, applies_to, slot) — the
    default, or the text named by `code` — after both fallback chains."""

    dealership = get_dealership_or_404(db, dealership_id)
    _location_of(db, dealership_id, location_id)
    chain = language_chain(language, dealership.default_correspondence_language)
    owned = StandardText.dealership_id == dealership_id
    own_scope = (
        owned & StandardText.location_id.is_(None)
        if location_id is None
        else owned & (StandardText.location_id.is_(None) | (StandardText.location_id == location_id))
    )
    query = select(StandardText).where(
        StandardText.category == category,
        StandardText.applies_to == applies_to,
        StandardText.slot == slot,
        StandardText.language.in_(chain),
        or_(StandardText.dealership_id.is_(None), own_scope),
    )
    if code is not None:
        query = query.where(StandardText.code == code)
    rows = list(db.scalars(query).all())
    for wanted in chain:
        effective = _effective_rows(row for row in rows if row.language == wanted)
        if code is not None:
            if effective:
                return effective[0]
            continue
        defaults = sorted(
            (row for row in effective if row.is_default), key=lambda row: (_rank(row), row.sort_order, row.code)
        )
        if defaults:
            return defaults[0]
    return None


@dataclass(frozen=True)
class RenderedStandardText:
    """What a module freezes into its document: the rendered wording plus
    which text and language it came from."""

    text: str
    code: str
    language: SwissLanguage
    standard_text_id: uuid.UUID
    version: int


def render_standard_text(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    category: StandardTextCategory,
    applies_to: StandardTextAppliesTo,
    slot: StandardTextSlot,
    language: SwissLanguage,
    values: Mapping[str, str | None],
    location_id: uuid.UUID | None = None,
    code: str | None = None,
) -> RenderedStandardText | None:
    """The resolver other contexts call through `app.platform.public`.

    Resolves the text (both fallback chains), then renders its placeholders:
    platform fills `dealership.name` and `location.phone`, the caller every
    other token it can (`values`, token name → value). An unknown, invalid,
    malformed or unresolvable token raises 422
    `standard_text_placeholder_unresolved` naming each (A-RULE-4) — never a
    blank, never the raw token. None when the slot has no text at all."""

    target = require_target(category, applies_to)
    _require_slot(target, slot)
    row = resolve_text(
        db,
        dealership_id=dealership_id,
        category=category,
        applies_to=applies_to,
        slot=slot,
        language=language,
        location_id=location_id,
        code=code,
    )
    if row is None:
        return None
    dealership = get_dealership_or_404(db, dealership_id)
    location = _location_of(db, dealership_id, location_id)
    merged = {**values, **platform_values(dealership, location)}
    rendered = render_body(
        row.body,
        target=target,
        values=merged,
        context={"code": row.code, "language": row.language.value},
    )
    return RenderedStandardText(
        text=rendered, code=row.code, language=row.language, standard_text_id=row.id, version=row.version
    )


def preview(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    category: StandardTextCategory,
    applies_to: StandardTextAppliesTo,
    language: SwissLanguage,
    body: str,
    location_id: uuid.UUID | None = None,
    today: dt.date | None = None,
) -> str:
    """The editor's preview (FR-A-07): the same renderer a document uses,
    on sample data, so a token that would fail a real render fails here."""

    target = require_target(category, applies_to)
    dealership = get_dealership_or_404(db, dealership_id)
    location = _location_of(db, dealership_id, location_id)
    values = {**sample_values(language, today=today or utcnow().date()), **platform_values(dealership, location)}
    return render_body(body, target=target, values=values)


# --- reads -------------------------------------------------------------------------


def list_texts(
    db: Session, *, dealership_id: uuid.UUID, category: StandardTextCategory | None = None
) -> list[StandardText]:
    """The library as the Betrieb sees it: every platform seed plus every
    row of its own (dealership and location scope)."""

    query = select(StandardText).where(
        or_(StandardText.dealership_id.is_(None), StandardText.dealership_id == dealership_id)
    )
    if category is not None:
        query = query.where(StandardText.category == category)
    return list(
        db.scalars(
            query.order_by(
                StandardText.category,
                StandardText.applies_to,
                StandardText.slot,
                StandardText.sort_order,
                StandardText.code,
                StandardText.language,
                StandardText.dealership_id.is_not(None),
                StandardText.location_id.is_not(None),
            )
        ).all()
    )


def seed_versions(db: Session, rows: Iterable[StandardText]) -> dict[uuid.UUID, int]:
    """The current version of every seed the given overrides point at."""

    ids = {row.overridden_from_id for row in rows if row.overridden_from_id is not None}
    if not ids:
        return {}
    return {
        seed_id: version
        for seed_id, version in db.execute(
            select(StandardText.id, StandardText.version).where(StandardText.id.in_(ids))
        ).all()
    }


def seed_changed(row: StandardText, versions: Mapping[uuid.UUID, int]) -> bool:
    """FR-A-29's "the standard text has changed, review yours": the seed this
    override points at moved past the version the Betrieb last reviewed."""

    if row.overridden_from_id is None:
        return False
    current = versions.get(row.overridden_from_id)
    return current is not None and current > (row.overridden_seed_version or 0)


def get_dealer_text_or_404(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    category: StandardTextCategory,
    text_id: uuid.UUID,
    for_update: bool = False,
) -> StandardText:
    """A Betrieb's own row, looked up BY its category (the delegation gate
    reads the path's category — core/permissions.require_write_or_delegated)
    — a seed, another Betrieb's row or another category's is 404."""

    query = select(StandardText).where(
        StandardText.id == text_id,
        StandardText.dealership_id == dealership_id,
        StandardText.category == category,
    )
    if for_update:
        query = query.with_for_update(key_share=True)
    row = db.scalar(query)
    if row is None:
        raise NotFoundError(f"Standard text {text_id} was not found.")
    return row


# --- dealer writes -----------------------------------------------------------------


def _snapshot(row: StandardText) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in _AUDITED_FIELDS:
        value = getattr(row, field)
        if isinstance(value, uuid.UUID):
            value = str(value)
        elif hasattr(value, "value"):
            value = value.value
        out[field] = value
    return out


def _validate_whatsapp_fields(
    category: StandardTextCategory, template_approved: bool, provider_template_id: str | None
) -> None:
    if category != StandardTextCategory.WHATSAPP:
        if template_approved or provider_template_id:
            raise _refuse(
                "standard_text_template_whatsapp_only",
                "Template approval applies to WhatsApp texts only (FR-A-54).",
            )
        return
    if template_approved and not (provider_template_id or "").strip():
        raise _refuse(
            "standard_text_template_id_required",
            "An approved WhatsApp template needs the provider's template id (FR-A-54).",
        )


def _demote_other_defaults(db: Session, row: StandardText, *, actor_id: uuid.UUID | None) -> None:
    """At most one default per slot, language and scope of this Betrieb.
    A demoted row changes, so its version moves and it is audited."""

    query = select(StandardText).where(
        StandardText.id != row.id,
        StandardText.dealership_id == row.dealership_id,
        StandardText.category == row.category,
        StandardText.applies_to == row.applies_to,
        StandardText.slot == row.slot,
        StandardText.language == row.language,
        StandardText.is_default.is_(True),
    )
    query = query.where(
        StandardText.location_id.is_(None)
        if row.location_id is None
        else StandardText.location_id == row.location_id
    )
    for other in db.scalars(query.with_for_update(key_share=True)).all():
        other.is_default = False
        other.version += 1
        other.updated_by = actor_id
        record_audit_event(
            db,
            entity_type="standard_text",
            entity_id=other.id,
            tenant_id=row.dealership_id,
            action="update",
            actor_id=actor_id,
            before={"is_default": True},
            after={"is_default": False},
            reason="another text became the default",
        )


@dataclass
class DealerTextInput:
    code: str | None
    language: SwissLanguage
    title: str
    body: str
    applies_to: StandardTextAppliesTo | None
    slot: StandardTextSlot | None
    location_id: uuid.UUID | None
    is_default: bool | None
    active: bool
    sort_order: int | None
    template_approved: bool
    provider_template_id: str | None


def _rows_of_code(db: Session, *, code: str, dealership_id: uuid.UUID | None) -> list[StandardText]:
    owner = StandardText.dealership_id.is_(None) if dealership_id is None else StandardText.dealership_id == dealership_id
    return list(db.scalars(select(StandardText).where(StandardText.code == code, owner)).all())


def create_dealer_text(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    category: StandardTextCategory,
    data: DealerTextInput,
    actor_id: uuid.UUID | None,
) -> StandardText:
    """A Betrieb's own text, a further language or location variant of one,
    or an override of a seed (when `code` names a seed: the seed's target
    and slot are taken, and the row points at the seed of that language)."""

    get_dealership_or_404(db, dealership_id)
    location = _location_of(db, dealership_id, data.location_id)

    seed: StandardText | None = None
    family: list[StandardText] = []
    if data.code is not None:
        seeds = _rows_of_code(db, code=data.code, dealership_id=None)
        family = seeds or _rows_of_code(db, code=data.code, dealership_id=dealership_id)
        seed = next((row for row in seeds if row.language == data.language), None)
        if seeds and seed is None:
            raise _refuse(
                "standard_text_seed_language_missing",
                f"The standard text {data.code} has no {data.language.value} version to adapt.",
            )
        if not family:
            # A new text's code is the server's (below); a code the client
            # names must already be a seed's or one of the Betrieb's own.
            raise _refuse("standard_text_code_unknown", f"No standard text {data.code} exists.")
    if family:
        anchor = family[0]
        if anchor.category != category:
            raise _refuse(
                "standard_text_category_mismatch",
                f"{data.code} is a {anchor.category.value} text, not {category.value}.",
            )
        for given, actual in ((data.applies_to, anchor.applies_to), (data.slot, anchor.slot)):
            if given is not None and given != actual:
                raise _refuse(
                    "standard_text_code_target_mismatch",
                    f"{data.code} is used on {anchor.applies_to.value} / {anchor.slot.value}.",
                )
        applies_to, slot = anchor.applies_to, anchor.slot
    else:
        if data.applies_to is None or data.slot is None:
            raise _refuse("standard_text_target_required", "A new text names where it is used (appliesTo, slot).")
        applies_to, slot = data.applies_to, data.slot

    target = require_target(category, applies_to)
    _require_slot(target, slot)
    validate_body(data.body, target)
    _validate_whatsapp_fields(category, data.template_approved, data.provider_template_id)

    code = data.code or f"{applies_to.value}.{slot.value}.{uuid7().hex[-12:]}"
    existing = db.scalar(
        select(StandardText.id).where(
            StandardText.dealership_id == dealership_id,
            StandardText.code == code,
            StandardText.language == data.language,
            StandardText.location_id.is_(None)
            if location is None
            else StandardText.location_id == location.id,
        )
    )
    if existing is not None:
        raise _refuse(
            "standard_text_exists",
            f"{code} already has a {data.language.value} text at this scope — edit that one.",
            standardTextId=str(existing),
        )

    row = StandardText(
        dealership_id=dealership_id,
        location_id=location.id if location is not None else None,
        scope=StandardTextScope.LOCATION if location is not None else StandardTextScope.DEALERSHIP,
        code=code,
        category=category,
        applies_to=applies_to,
        slot=slot,
        language=data.language,
        title=data.title.strip(),
        body=data.body,
        source=StandardTextSource.DEALER,
        overridden_from_id=seed.id if seed is not None else None,
        overridden_seed_version=seed.version if seed is not None else None,
        is_default=data.is_default if data.is_default is not None else (seed.is_default if seed else False),
        active=data.active,
        sort_order=data.sort_order if data.sort_order is not None else (seed.sort_order if seed else 100),
        template_approved=data.template_approved,
        provider_template_id=(data.provider_template_id or "").strip() or None,
        version=1,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(row)
    db.flush()
    if row.is_default:
        _demote_other_defaults(db, row, actor_id=actor_id)
    record_audit_event(
        db,
        entity_type="standard_text",
        entity_id=row.id,
        tenant_id=dealership_id,
        action="create",
        actor_id=actor_id,
        before=None,
        after=_snapshot(row),
    )
    db.commit()
    db.refresh(row)
    return row


def update_dealer_text(
    db: Session,
    *,
    row: StandardText,
    changes: Mapping[str, Any],
    if_match_version: int,
    actor_id: uuid.UUID | None,
) -> StandardText:
    """PATCH of a Betrieb's own row. `seed_reviewed` (an override only)
    records that the current seed version was reviewed — it clears "the
    standard text has changed"."""

    check_version(row.version, if_match_version, entity_name="StandardText")
    before = _snapshot(row)
    target = require_target(row.category, row.applies_to)
    for field in ("title", "body", "is_default", "active", "sort_order", "template_approved", "provider_template_id"):
        if field in changes:
            value = changes[field]
            if field == "title":
                value = str(value).strip()
            if field == "provider_template_id":
                value = (value or "").strip() or None
            setattr(row, field, value)
    validate_body(row.body, target)
    _validate_whatsapp_fields(row.category, row.template_approved, row.provider_template_id)
    if changes.get("seed_reviewed"):
        if row.overridden_from_id is None:
            raise _refuse("standard_text_not_an_override", "Only an adapted standard text has a seed to review.")
        seed = db.get(StandardText, row.overridden_from_id)
        if seed is not None:
            row.overridden_seed_version = seed.version
    after = _snapshot(row)
    if after == before:
        return row
    row.version += 1
    row.updated_by = actor_id
    db.flush()
    if row.is_default and not before["is_default"]:
        _demote_other_defaults(db, row, actor_id=actor_id)
    record_audit_event(
        db,
        entity_type="standard_text",
        entity_id=row.id,
        tenant_id=row.dealership_id,
        action="update",
        actor_id=actor_id,
        before={k: v for k, v in before.items() if after[k] != v},
        after={k: v for k, v in after.items() if before[k] != v},
    )
    db.commit()
    db.refresh(row)
    return row


def delete_dealer_text(db: Session, *, row: StandardText, if_match_version: int, actor_id: uuid.UUID | None) -> None:
    """Removes a Betrieb's own row; an override removed is the seed again."""

    check_version(row.version, if_match_version, entity_name="StandardText")
    record_audit_event(
        db,
        entity_type="standard_text",
        entity_id=row.id,
        tenant_id=row.dealership_id,
        action="delete",
        actor_id=actor_id,
        before=_snapshot(row),
        after=None,
    )
    db.delete(row)
    db.commit()


# --- platform seeds (FR-A-29: platform_admin, the Translations panel — KAN-209) ------


def list_platform_seeds(db: Session) -> list[StandardText]:
    return list(
        db.scalars(
            select(StandardText)
            .where(StandardText.dealership_id.is_(None))
            .order_by(StandardText.code, StandardText.language)
        ).all()
    )


def get_platform_seed_or_404(db: Session, seed_id: uuid.UUID, *, for_update: bool = False) -> StandardText:
    query = select(StandardText).where(StandardText.id == seed_id, StandardText.dealership_id.is_(None))
    if for_update:
        query = query.with_for_update(key_share=True)
    row = db.scalar(query)
    if row is None:
        raise NotFoundError(f"Standard text {seed_id} was not found.")
    return row


def update_platform_seed(
    db: Session,
    *,
    seed: StandardText,
    changes: Mapping[str, Any],
    if_match_version: int,
    actor_id: uuid.UUID | None,
) -> StandardText:
    """A platform_admin edit of one seed (code × language), versioned. It
    never touches a dealer's override — the bump of `version` is what every
    override pointing here reads as "the standard text has changed, review
    yours" (seed_changed). The caller has checked the principal is staff."""

    check_version(seed.version, if_match_version, entity_name="StandardText")
    before = _snapshot(seed)
    for field in ("title", "body", "active", "sort_order", "is_default"):
        if field in changes:
            setattr(seed, field, changes[field])
    validate_body(seed.body, require_target(seed.category, seed.applies_to))
    after = _snapshot(seed)
    if after == before:
        return seed
    seed.version += 1
    seed.updated_by = actor_id
    db.flush()
    record_audit_event(
        db,
        entity_type="standard_text",
        entity_id=seed.id,
        tenant_id=None,
        action="seed_update",
        actor_id=actor_id,
        before={k: v for k, v in before.items() if after[k] != v},
        after={k: v for k, v in after.items() if before[k] != v},
    )
    db.commit()
    db.refresh(seed)
    return seed


def seed_rows() -> list[dict[str, Any]]:
    """The starter library as plain row dicts — what the KAN-208 migration
    inserts and `install_platform_seeds` adds (enum values, not members)."""

    now = utcnow()
    rows: list[dict[str, Any]] = []
    for seed in SEED_TEXTS:
        for language in SwissLanguage:
            rows.append(
                {
                    "id": uuid7(),
                    "dealership_id": None,
                    "location_id": None,
                    "scope": StandardTextScope.DEALERSHIP.value,
                    "code": seed.code,
                    "category": seed.category.value,
                    "applies_to": seed.applies_to.value,
                    "slot": seed.slot.value,
                    "language": language.value,
                    "title": seed.titles[language],
                    "body": seed.bodies[language],
                    "source": StandardTextSource.PLATFORM_SEED.value,
                    "overridden_from_id": None,
                    "overridden_seed_version": None,
                    "is_default": seed.is_default,
                    "active": True,
                    "sort_order": seed.sort_order,
                    "template_approved": False,
                    "provider_template_id": None,
                    "version": 1,
                    "created_at": now,
                    "updated_at": now,
                    "created_by": None,
                    "updated_by": None,
                }
            )
    return rows


def install_platform_seeds(db: Session) -> int:
    """Adds every seed (code × language) not installed yet; never changes an
    installed one. Idempotent. Returns how many rows it added."""

    installed = {
        (code, language)
        for code, language in db.execute(
            select(StandardText.code, StandardText.language).where(StandardText.dealership_id.is_(None))
        ).all()
    }
    added = 0
    for values in seed_rows():
        if (values["code"], SwissLanguage(values["language"])) in installed:
            continue
        row = StandardText(
            **{
                **values,
                "scope": StandardTextScope(values["scope"]),
                "category": StandardTextCategory(values["category"]),
                "applies_to": StandardTextAppliesTo(values["applies_to"]),
                "slot": StandardTextSlot(values["slot"]),
                "language": SwissLanguage(values["language"]),
                "source": StandardTextSource(values["source"]),
            }
        )
        db.add(row)
        added += 1
    db.flush()
    return added
