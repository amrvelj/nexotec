"""What another context's nightly reconciliation checks against platform's
`document_link` table (rule 10; ADR-107), built here so the mapped
`DocumentLink` class never leaves platform (ADR-162; KAN-795).

Integration's delivery record names the secure link an SMS or a WhatsApp
message carried (`integration_message_delivery.document_link_id`, KAN-217
PR-5). Nothing purges a link (an expired one stays, answering 404), so an id
naming no row is drift, never expected.

A builder only: it reads nothing itself — the reconciliation run executes it.
Confined to the reconciliation modules like the organisation and identity
builders (`tests/architecture/test_platform_predicates_only_in_reconciliation.py`).
"""

from collections.abc import Callable
from typing import Any

from sqlalchemy import ColumnElement
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute

from app.core.reconciliation import ReferenceCheck
from app.platform.documents.models.document_link import DocumentLink


def document_link_reference_check(
    *,
    label: str,
    source_model: type[DeclarativeBase],
    source_row_id_column: InstrumentedAttribute[Any],
    source_fk_column: InstrumentedAttribute[Any],
    nullable: bool = False,
    source_where: Callable[[], ColumnElement[bool]] | None = None,
) -> ReferenceCheck:
    """`source_fk_column` names a `document_link` row."""

    return ReferenceCheck(
        label=label,
        source_model=source_model,
        source_row_id_column=source_row_id_column,
        source_fk_column=source_fk_column,
        target_model=DocumentLink,
        target_id_column=DocumentLink.id,
        nullable=nullable,
        source_where=source_where,
    )
