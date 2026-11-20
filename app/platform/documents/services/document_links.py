"""Secure document links (KAN-217 PR-4; Dealer Administration FR-A-52,
ADR-192): the link an SMS or WhatsApp send carries in place of the PDF.

**Who renders.** Platform is the lowest layer and never imports a module above
it (ADR-131). A module that registers a document message type also registers a
**document source** for it — a function that renders one of its documents for
a Betrieb, or answers `None` when that Betrieb holds no such document — from
the composition root `app/message_type_registry.py`, as it registers the type
itself. Serving a link calls that function with the link's own Betrieb, so a
link reads nothing outside the Betrieb that issued it.

**Issuing** (`issue_document_link`) is the send's step (PR-5): it writes the row
in the caller's transaction and hands back the raw token once; only its SHA-256
is stored. The URL carries it in the fragment (`/d#<token>`), never in a path
or a query a proxy or an access log keeps; the SPA's page sends it in a header. **Opening** (`open_document_link`) is unauthenticated: the token is
the whole authority. Unknown, malformed, expired, rate-limited, an offboarded
Betrieb's, or naming a document its Betrieb does not hold — every one of them
is the same 404 (`None` here), so an answer confirms nothing.
"""

import dataclasses
import datetime as dt
import hashlib
import logging
import re
import secrets
import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.config import get_settings
from app.platform.documents.models.document_link import (
    LINK_VALID_DAYS,
    OPEN_WINDOW,
    OPENS_PER_WINDOW,
    DocumentLink,
)
from app.platform.documents.services.message_types import (
    MessageTypeRegistrationError,
    get_message_type,
)
from app.platform.organisation.models.dealership import Dealership, DealershipStatus

logger = logging.getLogger(__name__)

# The SPA's public page; the token follows in the fragment, which no browser
# sends to a server (ADR-193): the page hands it to the API in TOKEN_HEADER.
LINK_PAGE = "/d#"
TOKEN_HEADER = "X-Document-Link"

# secrets.token_urlsafe(32): 43 characters of the URL-safe alphabet. Anything
# else is refused before the database is asked.
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


@dataclasses.dataclass(frozen=True)
class RenderedDocument:
    """What a document source hands back: the PDF and the file name the
    customer's browser shows."""

    pdf: bytes
    filename: str


# (db, tenant_id, document_id) -> the rendered document, or None when that
# Betrieb holds no such document.
DocumentSource = Callable[[Session, uuid.UUID, uuid.UUID], RenderedDocument | None]

_SOURCES: dict[str, DocumentSource] = {}


def register_document_source(message_type_code: str, source: DocumentSource) -> None:
    """Called from the composition root for each document message type a
    module can send by link. The type must be registered first and carry a
    document; registering the same function again is a no-op, another one for
    the same type is refused at start-up."""

    message_type = get_message_type(message_type_code)
    if message_type is None:
        raise MessageTypeRegistrationError(f"Document source for unregistered message type {message_type_code!r}.")
    if not message_type.carries_document:
        raise MessageTypeRegistrationError(f"Message type {message_type_code!r} carries no document to link to.")
    existing = _SOURCES.get(message_type_code)
    if existing is not None and existing is not source:
        raise MessageTypeRegistrationError(f"Message type {message_type_code!r} already has a document source.")
    _SOURCES[message_type_code] = source


def has_document_source(message_type_code: str) -> bool:
    return message_type_code in _SOURCES


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class IssuedDocumentLink:
    link_id: uuid.UUID
    # The raw token, returned once: it goes into the message and is never
    # stored or logged.
    token: str
    url: str
    expires_at: dt.datetime


def link_url(token: str) -> str:
    return get_settings().app_base_url.rstrip("/") + LINK_PAGE + token


def issue_document_link(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    message_type: str,
    document_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    now: dt.datetime | None = None,
) -> IssuedDocumentLink:
    """Writes a link to `document_id` of `tenant_id` (flushed, not committed:
    the send's transaction holds it). The caller has already read the
    document in that Betrieb; serving re-reads it there in any case. A type
    without a registered source is a defect, not a user error: ValueError."""

    if message_type not in _SOURCES:
        raise ValueError(f"No document source is registered for message type {message_type!r}.")
    issued_at = now or utcnow()
    raw = secrets.token_urlsafe(32)
    link = DocumentLink(
        tenant_id=tenant_id,
        message_type=message_type,
        document_id=document_id,
        token_hash=hash_token(raw),
        expires_at=issued_at + dt.timedelta(days=LINK_VALID_DAYS),
        open_count=0,
        window_opens=0,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(link)
    db.flush()
    return IssuedDocumentLink(link_id=link.id, token=raw, url=link_url(raw), expires_at=link.expires_at)


def _within_rate(link: DocumentLink, now: dt.datetime) -> bool:
    """Claims one open in the link's rolling window; False once it is spent."""

    if link.window_started_at is None or link.window_started_at + OPEN_WINDOW <= now:
        link.window_started_at = now
        link.window_opens = 0
    if link.window_opens >= OPENS_PER_WINDOW:
        return False
    link.window_opens += 1
    return True


def open_document_link(db: Session, raw_token: str, *, now: dt.datetime | None = None) -> RenderedDocument | None:
    """The document behind a link, or None (the route's uniform 404). Counts
    the open on the row and commits it — the read is the customer's, there is
    no other transaction to join."""

    if not _TOKEN.fullmatch(raw_token):
        return None
    at = now or utcnow()
    link = db.scalar(select(DocumentLink).where(DocumentLink.token_hash == hash_token(raw_token)).with_for_update())
    if link is None or link.expires_at <= at:
        db.rollback()
        return None
    status = db.scalar(select(Dealership.status).where(Dealership.id == link.tenant_id))
    if status is None or status == DealershipStatus.OFFBOARDED:
        db.rollback()
        return None
    source = _SOURCES.get(link.message_type)
    if source is None:
        logger.error("document link %s names message type %r with no document source", link.id, link.message_type)
        db.rollback()
        return None
    if not _within_rate(link, at):
        logger.warning("document link %s refused: more than %d opens in its window", link.id, OPENS_PER_WINDOW)
        db.commit()
        return None
    rendered = source(db, link.tenant_id, link.document_id)
    if rendered is None:
        logger.warning("document link %s names a document its Betrieb does not hold", link.id)
        db.rollback()
        return None
    if link.first_opened_at is None:
        link.first_opened_at = at
    link.last_opened_at = at
    link.open_count += 1
    db.commit()
    return rendered
