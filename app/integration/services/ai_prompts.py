"""The AI prompt library (KAN-227, D-A-33, FR-A-38) — the standard texts'
seed-and-override pattern (`app/platform/services/standard_text.py`), on a
closed set of codes.

**Resolution**: for a code, the language asked for, then the Betrieb's
`default_correspondence_language`, then DE (duplicates dropped); in each
language the Betrieb's active override wins over the seed. An override
switched off masks nothing — the seed speaks again (unlike a standard text,
there is no "switch the seed off": a code without a prompt is a feature that
cannot run).

**Writes**: a Betrieb writes only its overrides (`ai_prompts`, the manager);
an override is created from a seed of the same code and language and
remembers the seed version it was taken from. A seed is written by
`platform_admin` only, through the Translations panel (`ui_translations`,
D-A-23); every seed edit moves its version, which every override of it reads
as «the default prompt has changed, review yours» — derived on read
(`seed_changed`), cleared by `seed_reviewed`. Deleting an override is
«back to the default». Every write is audited (entity `integration_ai_prompt`)
and commits here.
"""

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.concurrency import check_version
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.i18n import SwissLanguage
from app.core.uuid7 import uuid7
from app.integration.models.ai_prompt import AiPromptSource, IntegrationAiPrompt
from app.integration.services.ai_prompt_registry import require_code, validate_prompt
from app.integration.services.ai_prompt_seed import SEED_PROMPTS, shipped_prompt

FALLBACK_LANGUAGE = SwissLanguage.DE
_ENTITY = "integration_ai_prompt"
_AUDITED = ("code", "language", "title", "system_prompt", "user_prompt", "active", "overridden_from_id", "overridden_seed_version")


def language_chain(requested: SwissLanguage, dealership_default: SwissLanguage | None) -> list[SwissLanguage]:
    chain = [requested]
    for language in (dealership_default, FALLBACK_LANGUAGE):
        if language is not None and language not in chain:
            chain.append(language)
    return chain


def _snapshot(row: IntegrationAiPrompt) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in _AUDITED:
        value = getattr(row, field)
        values[field] = value.value if hasattr(value, "value") else (str(value) if isinstance(value, uuid.UUID) else value)
    return values


def _audit(db: Session, row: IntegrationAiPrompt, *, action: str, actor_id, before, after) -> None:
    record_audit_event(
        db, entity_type=_ENTITY, entity_id=row.id, tenant_id=row.tenant_id, action=action, actor_id=actor_id,
        before=before, after=after,
    )


# --- reads ------------------------------------------------------------------------


def list_prompts(db: Session, *, tenant_id: uuid.UUID) -> list[IntegrationAiPrompt]:
    """The library as the Betrieb sees it: every seed plus its own overrides."""

    return list(
        db.scalars(
            select(IntegrationAiPrompt)
            .where(or_(IntegrationAiPrompt.tenant_id.is_(None), IntegrationAiPrompt.tenant_id == tenant_id))
            .order_by(IntegrationAiPrompt.code, IntegrationAiPrompt.language, IntegrationAiPrompt.tenant_id.is_not(None))
        ).all()
    )


def seed_versions(db: Session, rows: Iterable[IntegrationAiPrompt]) -> dict[uuid.UUID, int]:
    ids = {row.overridden_from_id for row in rows if row.overridden_from_id is not None}
    if not ids:
        return {}
    return dict(
        db.execute(select(IntegrationAiPrompt.id, IntegrationAiPrompt.version).where(IntegrationAiPrompt.id.in_(ids))).all()  # type: ignore[arg-type]
    )


def seed_changed(row: IntegrationAiPrompt, versions: Mapping[uuid.UUID, int]) -> bool:
    """«The default prompt has changed, review yours»: the seed this override
    points at moved past the version the Betrieb last reviewed."""

    if row.overridden_from_id is None:
        return False
    current = versions.get(row.overridden_from_id)
    return current is not None and current > (row.overridden_seed_version or 0)


def resolve_prompt(
    db: Session, *, tenant_id: uuid.UUID, code: str, languages: list[SwissLanguage]
) -> IntegrationAiPrompt | None:
    rows = db.scalars(
        select(IntegrationAiPrompt).where(
            IntegrationAiPrompt.code == code,
            IntegrationAiPrompt.language.in_(languages),
            or_(IntegrationAiPrompt.tenant_id.is_(None), IntegrationAiPrompt.tenant_id == tenant_id),
        )
    ).all()
    for language in languages:
        own = next((r for r in rows if r.language == language and r.tenant_id == tenant_id and r.active), None)
        if own is not None:
            return own
        seed = next((r for r in rows if r.language == language and r.tenant_id is None and r.active), None)
        if seed is not None:
            return seed
    return None


def get_dealer_prompt_or_404(
    db: Session, *, tenant_id: uuid.UUID, prompt_id: uuid.UUID, for_update: bool = False
) -> IntegrationAiPrompt:
    """A Betrieb's own override — a seed or another Betrieb's row is 404."""

    query = select(IntegrationAiPrompt).where(IntegrationAiPrompt.id == prompt_id, IntegrationAiPrompt.tenant_id == tenant_id)
    if for_update:
        query = query.with_for_update(key_share=True)
    row = db.scalar(query)
    if row is None:
        raise NotFoundError(f"AI prompt {prompt_id} was not found.")
    return row


def _seed_of(db: Session, code: str, language: SwissLanguage) -> IntegrationAiPrompt | None:
    return db.scalar(
        select(IntegrationAiPrompt).where(
            IntegrationAiPrompt.code == code,
            IntegrationAiPrompt.language == language,
            IntegrationAiPrompt.tenant_id.is_(None),
        )
    )


# --- dealer writes ----------------------------------------------------------------


def create_override(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    code: str,
    language: SwissLanguage,
    system_prompt: str,
    user_prompt: str,
    actor_id: uuid.UUID | None,
) -> IntegrationAiPrompt:
    entry = require_code(code)
    seed = _seed_of(db, code, language)
    if seed is None:
        raise UnprocessableEntityError(
            f"The AI prompt {code} has no {language.value} version to adapt.",
            details={"reason": "ai_prompt_seed_language_missing", "code": code, "language": language.value},
        )
    validate_prompt(entry, system_prompt=system_prompt, user_prompt=user_prompt)
    row = IntegrationAiPrompt(
        id=uuid7(),
        tenant_id=tenant_id,
        code=code,
        language=language,
        title=seed.title,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        source=AiPromptSource.DEALER,
        overridden_from_id=seed.id,
        overridden_seed_version=seed.version,
        active=True,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            f"The Betrieb has adapted {code} in {language.value} already — edit that one.",
            details={"reason": "ai_prompt_exists"},
        ) from exc
    _audit(db, row, action="create", actor_id=actor_id, before=None, after=_snapshot(row))
    db.commit()
    db.refresh(row)
    return row


def update_override(
    db: Session, *, row: IntegrationAiPrompt, changes: Mapping[str, Any], if_match_version: int, actor_id: uuid.UUID | None
) -> IntegrationAiPrompt:
    check_version(row.version, if_match_version, entity_name="IntegrationAiPrompt")
    before = _snapshot(row)
    for field in ("system_prompt", "user_prompt", "active"):
        if field in changes and changes[field] is not None:
            setattr(row, field, changes[field])
    validate_prompt(require_code(row.code), system_prompt=row.system_prompt, user_prompt=row.user_prompt)
    if changes.get("seed_reviewed") and row.overridden_from_id is not None:
        seed = db.get(IntegrationAiPrompt, row.overridden_from_id)
        if seed is not None:
            row.overridden_seed_version = seed.version
    after = _snapshot(row)
    if after == before:
        return row
    row.version += 1
    row.updated_by = actor_id
    db.flush()
    _audit(
        db, row, action="update", actor_id=actor_id,
        before={k: v for k, v in before.items() if after[k] != v},
        after={k: v for k, v in after.items() if before[k] != v},
    )
    db.commit()
    db.refresh(row)
    return row


def delete_override(db: Session, *, row: IntegrationAiPrompt, if_match_version: int, actor_id: uuid.UUID | None) -> None:
    """Back to the default: the seed speaks again."""

    check_version(row.version, if_match_version, entity_name="IntegrationAiPrompt")
    _audit(db, row, action="delete", actor_id=actor_id, before=_snapshot(row), after=None)
    db.delete(row)
    db.commit()


# --- platform seeds (platform_admin, the Translations panel) -----------------------


def list_seeds(db: Session, *, q: str | None = None) -> list[IntegrationAiPrompt]:
    rows = list(
        db.scalars(
            select(IntegrationAiPrompt)
            .where(IntegrationAiPrompt.tenant_id.is_(None))
            .order_by(IntegrationAiPrompt.code, IntegrationAiPrompt.language)
        ).all()
    )
    if q:
        needle = q.strip().casefold()
        hits = {
            row.code
            for row in rows
            if any(needle in value.casefold() for value in (row.code, row.title, row.system_prompt, row.user_prompt))
        }
        rows = [row for row in rows if row.code in hits]
    return rows


def get_seed_or_404(db: Session, seed_id: uuid.UUID, *, for_update: bool = False) -> IntegrationAiPrompt:
    query = select(IntegrationAiPrompt).where(IntegrationAiPrompt.id == seed_id, IntegrationAiPrompt.tenant_id.is_(None))
    if for_update:
        query = query.with_for_update(key_share=True)
    row = db.scalar(query)
    if row is None:
        raise NotFoundError(f"AI prompt {seed_id} was not found.")
    return row


def update_seed(
    db: Session,
    *,
    seed: IntegrationAiPrompt,
    changes: Mapping[str, Any],
    if_match_version: int,
    actor_id: uuid.UUID | None,
    action: str = "seed_update",
) -> IntegrationAiPrompt:
    check_version(seed.version, if_match_version, entity_name="IntegrationAiPrompt")
    before = _snapshot(seed)
    for field in ("title", "system_prompt", "user_prompt"):
        if field in changes and changes[field] is not None:
            setattr(seed, field, changes[field])
    validate_prompt(require_code(seed.code), system_prompt=seed.system_prompt, user_prompt=seed.user_prompt)
    after = _snapshot(seed)
    if after == before:
        return seed
    seed.version += 1
    seed.updated_by = actor_id
    db.flush()
    _audit(
        db, seed, action=action, actor_id=actor_id,
        before={k: v for k, v in before.items() if after[k] != v},
        after={k: v for k, v in after.items() if before[k] != v},
    )
    db.commit()
    db.refresh(seed)
    return seed


def revert_seed(db: Session, *, seed: IntegrationAiPrompt, if_match_version: int, actor_id: uuid.UUID | None) -> IntegrationAiPrompt:
    shipped = shipped_prompt(seed.code, seed.language)
    if shipped is None:
        check_version(seed.version, if_match_version, entity_name="IntegrationAiPrompt")
        raise UnprocessableEntityError(
            "Nexotec ships no wording for this prompt; there is nothing to restore.",
            details={"reason": "ai_prompt_seed_not_shipped", "code": seed.code},
        )
    title, system_prompt, user_prompt = shipped
    return update_seed(
        db, seed=seed, changes={"title": title, "system_prompt": system_prompt, "user_prompt": user_prompt},
        if_match_version=if_match_version, actor_id=actor_id, action="seed_revert",
    )


def seed_rows() -> list[dict[str, Any]]:
    """The shipped prompts as plain row dicts (enum values, not members)."""

    now = utcnow()
    return [
        {
            "id": uuid7(),
            "tenant_id": None,
            "code": seed.code,
            "language": language.value,
            "title": seed.titles[language],
            "system_prompt": seed.system_prompts[language],
            "user_prompt": seed.user_prompts[language],
            "source": AiPromptSource.PLATFORM_SEED.value,
            "overridden_from_id": None,
            "overridden_seed_version": None,
            "active": True,
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "created_by": None,
            "updated_by": None,
        }
        for seed in SEED_PROMPTS
        for language in SwissLanguage
    ]


def install_seeds(db: Session) -> int:
    """Adds every shipped prompt not installed yet (the test fixture; the
    migration installs them in a deployed database). Idempotent."""

    installed = set(
        db.execute(
            select(IntegrationAiPrompt.code, IntegrationAiPrompt.language).where(IntegrationAiPrompt.tenant_id.is_(None))
        ).all()
    )
    added = 0
    for values in seed_rows():
        language = SwissLanguage(values["language"])
        if (values["code"], language) in installed:
            continue
        db.add(IntegrationAiPrompt(**{**values, "language": language, "source": AiPromptSource(values["source"])}))
        added += 1
    db.flush()
    return added
