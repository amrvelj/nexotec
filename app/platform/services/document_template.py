"""DocumentTemplate service layer (WP-6b PR-2): a plain optimistic-
concurrency CRUD, with one twist — the resource may not exist yet.
`get_document_template_or_default` answers version=0 for that state so the
ordinary `If-Match`/`check_version` machinery (app.core.concurrency) needs
no special-casing to create the row on its first PATCH; see
app.platform.schemas.document_template.DocumentTemplateRead's own
docstring for the full contract.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import ConflictError
from app.platform.models.document_template import (
    DEFAULT_FOOTER_COLUMNS,
    AddressWindow,
    DocumentTemplate,
    HeaderLayout,
    LogoSize,
)
from app.platform.schemas.document_template import DocumentTemplateRead, DocumentTemplateUpdate

_LANGUAGE_FIELDS = (
    "header_note_de",
    "header_note_fr",
    "header_note_it",
    "header_note_en",
)

# KAN-206: the letterhead layout and its shipped defaults — what a Betrieb
# without a row reads, and what a row created by its first PATCH starts from.
_LAYOUT_DEFAULTS: dict[str, Any] = {
    "header_layout": HeaderLayout.LOGO_LEFT,
    "logo_size": LogoSize.M,
    "address_window": AddressWindow.RIGHT,
    "footer_columns": [column.value for column in DEFAULT_FOOTER_COLUMNS],
    "draft_watermark": True,
    "page_numbers": True,
}
_FIELDS = (*_LANGUAGE_FIELDS, *_LAYOUT_DEFAULTS)


def _stored(value: Any) -> Any:
    """The value as the row holds it and the audit row records it."""

    if isinstance(value, list):
        return [_stored(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


def get_document_template(db: Session, dealership_id: uuid.UUID) -> DocumentTemplate | None:
    return db.query(DocumentTemplate).filter(DocumentTemplate.dealership_id == dealership_id).one_or_none()


def get_document_template_or_default(db: Session, dealership_id: uuid.UUID) -> DocumentTemplateRead:
    template = get_document_template(db, dealership_id)
    if template is None:
        return DocumentTemplateRead.model_validate(
            {
                "id": None,
                "dealership_id": dealership_id,
                **{field: None for field in _LANGUAGE_FIELDS},
                **_LAYOUT_DEFAULTS,
                "version": 0,
                "created_at": None,
                "updated_at": None,
            }
        )
    return DocumentTemplateRead.model_validate(template, from_attributes=True)


def upsert_document_template(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    data: DocumentTemplateUpdate,
    if_match_version: int,
    actor_id: uuid.UUID,
    commit: bool = True,
) -> DocumentTemplate:
    template = get_document_template(db, dealership_id)
    changes = {field: _stored(value) for field, value in data.model_dump(exclude_unset=True).items()}

    if template is None:
        if if_match_version != 0:
            raise ConflictError(
                "No document template exists yet for this dealership — If-Match must be 0 to create it.",
                details={"currentVersion": 0, "ifMatchVersion": if_match_version},
            )
        template = DocumentTemplate(dealership_id=dealership_id, version=1)
        for field in _LANGUAGE_FIELDS:
            setattr(template, field, changes.get(field))
        for field, default in _LAYOUT_DEFAULTS.items():
            setattr(template, field, changes.get(field, default))
        template.created_by = actor_id
        template.updated_by = actor_id
        db.add(template)
        db.flush()
        before: dict[str, Any] | None = None
        after: dict[str, Any] | None = {field: _stored(getattr(template, field)) for field in _FIELDS}
        action = "create"
    else:
        if template.version != if_match_version:
            raise ConflictError(
                f"DocumentTemplate has been modified since If-Match version {if_match_version} "
                f"(current version is {template.version}).",
                details={"currentVersion": template.version, "ifMatchVersion": if_match_version},
            )
        before = {}
        after = {}
        for field, value in changes.items():
            current = _stored(getattr(template, field))
            if current == value:
                continue
            before[field] = current
            after[field] = value
            setattr(template, field, value)
        template.updated_by = actor_id
        template.version += 1
        action = "update"

    if before or after:
        record_audit_event(
            db,
            entity_type="document_template",
            entity_id=template.id,
            tenant_id=dealership_id,
            action=action,
            actor_id=actor_id,
            before=before,
            after=after,
        )

    if commit:
        db.commit()
        db.refresh(template)
    else:
        db.flush()
    return template
