"""UI translations (D-A-23, FR-A-30…32; KAN-209) — the one writer of
`ui_translation`, `ui_translation_revision` and `missing_translation`.

- `runtime_map` is what `GET /v1/i18n/{language}` serves: the platform's
  published values only (see the model's docstring for why a `bundle_seed`
  row is never served — the shipped bundle is the fallback for it).
- `publish` / `revert` are platform staff's edits, one key in one language,
  If-Match on the row's version. Each bumps the global revision in the same
  transaction (under that row's lock, so two edits never share a version),
  and writes an audit row with before/after — the history of a string.
- `import_bundles` turns the frontend bundles into `bundle_seed` rows. It is
  idempotent and never touches a `platform` value: a re-run adds keys the
  bundles gained and refreshes the seed text of keys they changed.
- `record_missing` logs keys the SPA rendered as the loud marker.
"""

import base64
import dataclasses
import json
import re
from collections.abc import Iterable, Mapping

from sqlalchemy import func, or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import BadRequestError, NotFoundError, UnprocessableEntityError
from app.core.i18n import SwissLanguage
from app.core.pagination import count_capped
from app.platform.models.ui_translation import (
    MissingTranslation,
    UiTranslation,
    UiTranslationRevision,
    UiTranslationSource,
)

# The one row of `ui_translation_revision` (seeded by the KAN-209 migration).
UI_TRANSLATION_SCOPE = "ui"

# The log keeps at most this many distinct (key, language) rows: a report of
# a key beyond it is counted on an existing row or dropped, so an account that
# sends invented keys cannot grow the table without bound. A real missing key
# is a parity defect (localeKeyParity.test.ts keeps them from shipping), so
# the cap is far above anything a correct build reports.
MISSING_TRANSLATION_ROW_CAP = 5000

_AUDIT_ENTITY = "ui_translation"


# --- the global revision --------------------------------------------------------------------


def current_revision(db: Session) -> int:
    version = db.scalar(
        select(UiTranslationRevision.version).where(UiTranslationRevision.scope == UI_TRANSLATION_SCOPE)
    )
    return int(version or 0)


def _bump_revision(db: Session) -> int:
    """+1 on the global version, in the caller's transaction. The UPDATE holds
    the row's lock until commit, so concurrent publishes take turns and each
    commits a version of its own."""

    bumped = db.execute(
        update(UiTranslationRevision)
        .where(UiTranslationRevision.scope == UI_TRANSLATION_SCOPE)
        .values(version=UiTranslationRevision.version + 1, updated_at=utcnow())
        .returning(UiTranslationRevision.version)
    ).scalar()
    if bumped is not None:
        return int(bumped)
    # A database built without the migration's seed row (the test schema is
    # created from the models): insert it, or carry on with a concurrent
    # winner's row (the KAN-393 savepoint pattern).
    try:
        with db.begin_nested():
            db.add(UiTranslationRevision(scope=UI_TRANSLATION_SCOPE, version=1))
        return 1
    except IntegrityError:
        return _bump_revision(db)


def etag_for(language: SwissLanguage, version: int) -> str:
    return f'"ui-{version}-{language.value}"'


# --- the runtime map ------------------------------------------------------------------------


def runtime_map(db: Session, language: SwissLanguage) -> dict[str, str]:
    rows = db.execute(
        select(UiTranslation.namespace, UiTranslation.key, UiTranslation.value).where(
            UiTranslation.language == language, UiTranslation.source == UiTranslationSource.PLATFORM
        )
    ).all()
    return {f"{namespace}.{key}": value for namespace, key, value in rows}


# --- the panel's listing --------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class KeyGroup:
    namespace: str
    key: str
    rows: dict[SwissLanguage, UiTranslation]


def _encode_key_cursor(namespace: str, key: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([namespace, key]).encode("utf-8")).decode("ascii")


def _decode_key_cursor(cursor: str) -> tuple[str, str]:
    try:
        namespace, key = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        if not isinstance(namespace, str) or not isinstance(key, str):
            raise TypeError
        return namespace, key
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise BadRequestError("Cursor is malformed or invalid.") from exc


def _like_pattern(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_keys(
    db: Session,
    *,
    q: str | None,
    namespace: str | None,
    edited_only: bool,
    limit: int,
    cursor: str | None,
) -> tuple[list[KeyGroup], str | None, int, bool]:
    """Keys (not rows) in (namespace, key) order, each with its row per
    language. `q` matches the full key path or any language's live or seed
    text, case-insensitively; `edited_only` keeps keys with a platform value
    in at least one language."""

    filters = []
    if namespace:
        filters.append(UiTranslation.namespace == namespace)
    if q:
        pattern = _like_pattern(q.strip())
        filters.append(
            or_(
                (UiTranslation.namespace + "." + UiTranslation.key).ilike(pattern, escape="\\"),
                UiTranslation.value.ilike(pattern, escape="\\"),
                UiTranslation.seed_value.ilike(pattern, escape="\\"),
            )
        )
    if edited_only:
        filters.append(UiTranslation.source == UiTranslationSource.PLATFORM)

    keys = (
        select(UiTranslation.namespace, UiTranslation.key)
        .where(*filters)
        .group_by(UiTranslation.namespace, UiTranslation.key)
    )
    total, is_estimate = count_capped(db, keys, threshold=get_settings().count_exact_threshold)

    page = keys
    if cursor:
        page = page.having(tuple_(UiTranslation.namespace, UiTranslation.key) > _decode_key_cursor(cursor))
    page_keys = [
        (ns, k)
        for ns, k in db.execute(page.order_by(UiTranslation.namespace, UiTranslation.key).limit(limit + 1)).all()
    ]
    has_more = len(page_keys) > limit
    page_keys = page_keys[:limit]
    next_cursor = _encode_key_cursor(*page_keys[-1]) if has_more and page_keys else None

    groups: dict[tuple[str, str], KeyGroup] = {(ns, k): KeyGroup(ns, k, {}) for ns, k in page_keys}
    if page_keys:
        for row in db.scalars(
            select(UiTranslation).where(tuple_(UiTranslation.namespace, UiTranslation.key).in_(page_keys))
        ):
            groups[(row.namespace, row.key)].rows[row.language] = row
    return list(groups.values()), next_cursor, total, is_estimate


def list_namespaces(db: Session) -> list[str]:
    return list(db.scalars(select(UiTranslation.namespace).distinct().order_by(UiTranslation.namespace)))


# --- edits ----------------------------------------------------------------------------------


def get_row_for_update_or_404(db: Session, *, namespace: str, key: str, language: SwissLanguage) -> UiTranslation:
    row = db.scalar(
        select(UiTranslation)
        .where(UiTranslation.namespace == namespace, UiTranslation.key == key, UiTranslation.language == language)
        .with_for_update()
    )
    if row is None:
        raise NotFoundError(f"UI translation {namespace}.{key} ({language.value}) was not found.")
    return row


# `{{name}}`, `{{count, number}}`, `$t(other.key)` and a <Trans> tag such as
# `<name/>`: the parts of a string the code fills in. A published value must
# keep exactly the seed's — a dropped `{{count, number}}` renders a sentence
# without its number, a renamed one renders the braces.
_PLACEHOLDER = re.compile(r"\{\{\s*[^}]*?\s*\}\}|\$t\([^)]*\)|</?[A-Za-z0-9_]+\s*/?>")


def placeholders(text: str) -> list[str]:
    return sorted(re.sub(r"\s+", "", token) for token in _PLACEHOLDER.findall(text))


def _snapshot(row: UiTranslation) -> dict[str, object]:
    return {"value": row.value, "source": row.source.value, "version": row.version}


def _audit(db: Session, row: UiTranslation, *, action: str, before: dict[str, object], actor_id) -> None:
    record_audit_event(
        db,
        entity_type=_AUDIT_ENTITY,
        entity_id=row.id,
        tenant_id=None,  # Nexotec's strings, no Betrieb's (FR-A-32)
        action=action,
        actor_id=actor_id,
        before={"namespace": row.namespace, "key": row.key, "language": row.language.value, **before},
        after={"namespace": row.namespace, "key": row.key, "language": row.language.value, **_snapshot(row)},
    )


def publish(db: Session, *, row: UiTranslation, value: str, actor_id) -> UiTranslation:
    """Publishes `value` as the live text of one key in one language. The
    caller has locked the row and checked If-Match. Publishing the seed text
    itself is a revert (the row goes back to `bundle_seed`)."""

    if not value.strip():
        raise UnprocessableEntityError(
            "A UI string is never blank.", details={"reason": "ui_translation_blank", "field": "value"}
        )
    if placeholders(value) != placeholders(row.seed_value):
        raise UnprocessableEntityError(
            "The text must keep the placeholders of the shipped text.",
            details={
                "reason": "ui_translation_placeholders_changed",
                "field": "value",
                "expected": placeholders(row.seed_value),
            },
        )
    before = _snapshot(row)
    source = UiTranslationSource.BUNDLE_SEED if value == row.seed_value else UiTranslationSource.PLATFORM
    if value == row.value and source == row.source:
        return row  # nothing changes: no version, no audit row, no new ETag
    row.value = value
    row.source = source
    row.version += 1
    row.updated_by = actor_id
    db.flush()
    _bump_revision(db)
    _audit(db, row, action="ui_translation_published", before=before, actor_id=actor_id)
    return row


def revert(db: Session, *, row: UiTranslation, actor_id) -> UiTranslation:
    """Takes the platform's value back: the row serves the bundle again."""

    if row.source == UiTranslationSource.BUNDLE_SEED and row.value == row.seed_value:
        return row
    before = _snapshot(row)
    row.value = row.seed_value
    row.source = UiTranslationSource.BUNDLE_SEED
    row.version += 1
    row.updated_by = actor_id
    db.flush()
    _bump_revision(db)
    _audit(db, row, action="ui_translation_reverted", before=before, actor_id=actor_id)
    return row


# --- the bundle import ----------------------------------------------------------------------


@dataclasses.dataclass
class ImportReport:
    inserted: int = 0
    seed_refreshed: int = 0
    unchanged: int = 0
    # A platform row whose seed text the bundle has since changed: the
    # platform's value stays live; the new seed is what the panel diffs against.
    platform_rows_with_new_seed: list[str] = dataclasses.field(default_factory=list)
    # Bundle entries that are not a namespace object of strings.
    skipped: list[str] = dataclasses.field(default_factory=list)


def flatten_bundle(bundle: Mapping[str, object]) -> tuple[dict[tuple[str, str], str], list[str]]:
    """{namespace: {nested…}} → {(namespace, dotted key): text}, plus what
    could not be read as such (a top-level string, a non-string leaf)."""

    flat: dict[tuple[str, str], str] = {}
    skipped: list[str] = []

    def walk(namespace: str, prefix: str, node: Mapping[str, object]) -> None:
        for name, child in node.items():
            path = f"{prefix}.{name}" if prefix else name
            if isinstance(child, Mapping):
                walk(namespace, path, child)
            elif isinstance(child, str):
                flat[(namespace, path)] = child
            else:
                skipped.append(f"{namespace}.{path}")

    for namespace, node in bundle.items():
        if isinstance(node, Mapping):
            walk(namespace, "", node)
        else:
            skipped.append(namespace)
    return flat, skipped


def import_bundles(db: Session, bundles: Mapping[SwissLanguage, Mapping[str, object]]) -> ImportReport:
    """Writes `bundle_seed` rows for every string of every bundle, in the
    caller's transaction (the script commits or rolls back). Publishes no
    event and bumps no revision: a seed row is never served."""

    report = ImportReport()
    existing = {
        (row.namespace, row.key, row.language): row
        for row in db.scalars(select(UiTranslation).where(UiTranslation.language.in_(list(bundles))))
    }
    for language, bundle in bundles.items():
        flat, skipped = flatten_bundle(bundle)
        report.skipped.extend(f"{language.value}:{path}" for path in skipped)
        for (namespace, key), text in sorted(flat.items()):
            row = existing.get((namespace, key, language))
            if row is None:
                db.add(
                    UiTranslation(
                        namespace=namespace,
                        key=key,
                        language=language,
                        value=text,
                        seed_value=text,
                        source=UiTranslationSource.BUNDLE_SEED,
                    )
                )
                report.inserted += 1
            elif row.seed_value == text:
                report.unchanged += 1
            else:
                row.seed_value = text
                if row.source == UiTranslationSource.BUNDLE_SEED:
                    row.value = text
                else:
                    report.platform_rows_with_new_seed.append(f"{language.value}:{namespace}.{key}")
                row.version += 1
                report.seed_refreshed += 1
    db.flush()
    return report


# --- the missing-key log --------------------------------------------------------------------


def record_missing(db: Session, *, language: SwissLanguage, keys: Iterable[str]) -> int:
    """Counts each reported key once per report; returns how many were
    recorded (a key past the row cap that has no row yet is dropped)."""

    distinct = sorted(set(keys))
    if not distinct:
        return 0
    now = utcnow()
    known = set(
        db.scalars(
            select(MissingTranslation.key).where(
                MissingTranslation.language == language, MissingTranslation.key.in_(distinct)
            )
        )
    )
    if known:
        db.execute(
            update(MissingTranslation)
            .where(MissingTranslation.language == language, MissingTranslation.key.in_(known))
            .values(report_count=MissingTranslation.report_count + 1, last_reported_at=now)
        )
    recorded = len(known)
    room = MISSING_TRANSLATION_ROW_CAP - (db.scalar(select(func.count()).select_from(MissingTranslation)) or 0)
    for key in [k for k in distinct if k not in known][: max(room, 0)]:
        try:
            with db.begin_nested():
                db.add(MissingTranslation(key=key, language=language, first_reported_at=now, last_reported_at=now))
            recorded += 1
        except IntegrityError:
            # A concurrent report inserted it first: count this one on it.
            db.execute(
                update(MissingTranslation)
                .where(MissingTranslation.language == language, MissingTranslation.key == key)
                .values(report_count=MissingTranslation.report_count + 1, last_reported_at=now)
            )
            recorded += 1
    return recorded


def list_missing(db: Session, *, limit: int) -> list[MissingTranslation]:
    return list(
        db.scalars(
            select(MissingTranslation)
            .order_by(MissingTranslation.last_reported_at.desc(), MissingTranslation.key)
            .limit(limit)
        )
    )
