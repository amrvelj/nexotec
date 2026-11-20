"""A secure link to a customer document (KAN-217 PR-4; Dealer Administration
FR-A-52, ADR-193).

SMS and WhatsApp carry no attachment, so a send on those channels carries a
link instead (Anto's smaller points, 2026-11-14: valid for 14 days). The link
is served by the application, never by a bucket: the URL is `{app}/d#<token>`
(the fragment never reaches a server), the SPA's page sends the token in a
header to `GET /v1/document-links/current`, which renders the document again from its frozen content, as the signed-in download
does.

**A token in a database is a password** (the `auth_token` posture): only its
SHA-256 is stored; the raw value leaves once, to the caller that puts it into
the message. **Tenant-partitioned:** the row names the Betrieb that issued it,
and the document is read only inside that Betrieb — a link can never serve
another Betrieb's document, whatever id it carries.

`document_id` is the owning module's document (Sales' `sales_document` for the
offer and the contract) — a plain id with no foreign key (rule 2); the module
that registered the message type is its owner and renders it.

**Rate limit:** at most `OPENS_PER_WINDOW` renders per link in a rolling
`OPEN_WINDOW`; more answer 404 like an unknown link (the error taxonomy has no
429, and a 404 confirms nothing).
"""

import datetime as dt
import uuid

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin
from app.core.types import GUID, UTCDateTime
from app.db import Base

LINK_VALID_DAYS = 14
OPEN_WINDOW = dt.timedelta(hours=1)
OPENS_PER_WINDOW = 30


class DocumentLink(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "document_link"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_document_link_token_hash"),)

    # The registered message type the document was sent as (`offer`,
    # `sales_contract` …): it names the module that renders it.
    message_type: Mapped[str] = mapped_column(String(48), nullable=False)
    # The owning module's document id — owner: the message type's module
    # (rule 2: a plain id, no foreign key, no label: the link never shows it).
    document_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    # The dealer side's «opened» fact (FR-A-54's delivery record reads it, PR-5).
    first_opened_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_opened_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    open_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The rate limit's rolling window.
    window_started_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    window_opens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
