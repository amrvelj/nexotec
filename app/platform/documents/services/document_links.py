"""Secure document links (KAN-217 PR-4; Dealer Administration FR-A-52,
ADR-192, ADR-193): the link an SMS or WhatsApp send carries in place of the PDF.

**Who renders.** Platform is the lowest layer and never imports a module above
it (ADR-131). A module that registers a document message type also registers a
**document source** for it — a function that renders one of its documents for
a Betrieb, or answers `None` when that Betrieb holds no such document — from
the composition root `app/message_type_registry.py`, as it registers the type
itself. Serving a link calls that function with the link's own Betrieb, so a
link reads nothing outside the Betrieb that issued it.

**Issuing** (`issue_document_link`) is the send's step (PR-5): it writes and
commits the row on platform's own session (ADR-047, ADR-204) and hands back
the raw token once; only its SHA-256
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
from app.core.object_store import ObjectStore, StoredObject, get_object_store
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
from app.platform.organisation.models.branding_asset import BrandingAssetKind
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.organisation.services.branding import resolve_asset

logger = logging.getLogger(__name__)

# The SPA's public page; the token follows in the fragment, which no browser
# sends to a server (ADR-193): the page hands it to the API in TOKEN_HEADER.
LINK_PAGE = "/d"
TOKEN_HEADER = "X-Document-Link"

# secrets.token_urlsafe(32): 43 characters of the URL-safe alphabet. Anything
# else is refused before the database is asked.
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


@dataclasses.dataclass(frozen=True)
class RenderedDocument:
    """What a document source hands back: the PDF and the file name the
    contact's browser shows."""

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


_LANGUAGES = frozenset({"de", "fr", "it", "en"})


def link_url(token: str, language: object = None) -> str:
    """`{app}/d?lang=<contact's language>#<token>`: the page opens in the
    language the message was written in (not a secret, so it may be a query);
    the token only ever in the fragment."""

    # A `str` enum (the contact's `Language`) formats as its name: take the value.
    code = getattr(language, "value", language)
    query = f"?lang={code}" if code in _LANGUAGES else ""
    return get_settings().app_base_url.rstrip("/") + LINK_PAGE + query + "#" + token


def sample_document_link_url(language: object = None) -> str:
    """A link URL of exactly the length a real one has (a token is always 43
    characters): what a send checks its message against before it issues the
    link, so a refused send leaves no link behind (ADR-204). Opens nothing."""

    return link_url("_" * 43, language)


def issue_document_link(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    message_type: str,
    document_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    language: object = None,
    now: dt.datetime | None = None,
) -> IssuedDocumentLink:
    """Writes a link to `document_id` of `tenant_id` and commits it on a
    session of platform's own (ADR-047; KAN-217 PR-5, ADR-204 — the caller's
    session is neither written nor committed). `language` is the contact's
    correspondence language, which the page opens in. The caller has already read the
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
    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as own:
        own.add(link)
        own.commit()
    return IssuedDocumentLink(link_id=link.id, token=raw, url=link_url(raw, language), expires_at=link.expires_at)


def _window_spent(link: DocumentLink, now: dt.datetime) -> bool:
    """True while the link's rolling window holds no read to spare — the
    check alone, nothing claimed."""

    return (
        link.window_started_at is not None
        and link.window_started_at + OPEN_WINDOW > now
        and link.window_opens >= OPENS_PER_WINDOW
    )


def _within_rate(link: DocumentLink, now: dt.datetime) -> bool:
    """Claims one read in the link's rolling window; False once it is spent."""

    if link.window_started_at is None or link.window_started_at + OPEN_WINDOW <= now:
        link.window_started_at = now
        link.window_opens = 0
    if link.window_opens >= OPENS_PER_WINDOW:
        return False
    link.window_opens += 1
    return True


def _usable_link(db: Session, raw_token: str, at: dt.datetime, *, lock: bool) -> DocumentLink | None:
    """The link a token names while it may still be read: well-formed, known,
    not expired, its Betrieb not offboarded, its type still with a source.
    Anything else rolls back and answers None — the route's one 404. The rate
    window is the caller's to judge."""

    if not _TOKEN.fullmatch(raw_token):
        return None
    query = select(DocumentLink).where(DocumentLink.token_hash == hash_token(raw_token))
    link = db.scalar(query.with_for_update() if lock else query)
    if link is None or link.expires_at <= at:
        db.rollback()
        return None
    status = db.scalar(select(Dealership.status).where(Dealership.id == link.tenant_id))
    if status is None or status == DealershipStatus.OFFBOARDED:
        db.rollback()
        return None
    if link.message_type not in _SOURCES:
        logger.error("document link %s names message type %r with no document source", link.id, link.message_type)
        db.rollback()
        return None
    return link


def open_document_link(db: Session, raw_token: str, *, now: dt.datetime | None = None) -> RenderedDocument | None:
    """The document behind a link, or None (the route's uniform 404). Counts
    the open on the row and commits it — the read is the contact's, there is
    no other transaction to join."""

    at = now or utcnow()
    link = _usable_link(db, raw_token, at, lock=True)
    if link is None:
        return None
    if not _within_rate(link, at):
        logger.warning("document link %s refused: more than %d reads in its window", link.id, OPENS_PER_WINDOW)
        db.commit()
        return None
    rendered = _SOURCES[link.message_type](db, link.tenant_id, link.document_id)
    if rendered is None:
        logger.warning("document link %s names a document its business does not hold", link.id)
        db.rollback()
        return None
    if link.first_opened_at is None:
        link.first_opened_at = at
    link.last_opened_at = at
    link.open_count += 1
    db.commit()
    return rendered


# --- the sender (KAN-217 PR-7; Anto, 2026-11-20, option 1) -----------------------


@dataclasses.dataclass(frozen=True)
class LinkSender:
    """Whose page the contact is on: the Betrieb that issued the link. Read
    by link only — an unusable link names nobody."""

    name: str
    has_logo: bool


def read_link_sender(db: Session, raw_token: str, *, now: dt.datetime | None = None) -> LinkSender | None:
    """The issuing Betrieb's name (its trade name, else its legal name) and
    whether it has a logo, or None — refused exactly when opening the
    document would be refused before its render: unknown, malformed, expired,
    an offboarded Betrieb's, a type without a source, or a spent rate window.
    It does not render the document, so a link whose document is gone still
    names its sender: that is the page's «Wenden Sie sich an …» (ADR-208).

    Two indexed reads and no claim on the window: it costs what an unknown
    token's 404 costs, which nothing limits either. Writes nothing."""

    at = now or utcnow()
    link = _usable_link(db, raw_token, at, lock=False)
    if link is None:
        return None
    if _window_spent(link, at):
        db.rollback()
        return None
    dealership = db.get(Dealership, link.tenant_id)
    has_logo = resolve_asset(db, link.tenant_id, BrandingAssetKind.PRIMARY) is not None
    db.rollback()
    if dealership is None:
        return None
    return LinkSender(name=dealership.trade_name or dealership.legal_name, has_logo=has_logo)


def open_link_logo(
    db: Session, raw_token: str, *, store: ObjectStore | None = None, now: dt.datetime | None = None
) -> StoredObject | None:
    """The issuing Betrieb's primary logo, or None (the one 404): the link's
    refusals, no logo, or a file the store does not hold. Reading the file
    claims one read of the link's window, as opening the document does — a
    logo may weigh 5 MB."""

    at = now or utcnow()
    link = _usable_link(db, raw_token, at, lock=True)
    if link is None:
        return None
    asset = resolve_asset(db, link.tenant_id, BrandingAssetKind.PRIMARY)
    if asset is None:
        db.rollback()
        return None
    if not _within_rate(link, at):
        logger.warning("document link %s refused: more than %d reads in its window", link.id, OPENS_PER_WINDOW)
        db.commit()
        return None
    key = asset.object_key
    db.commit()
    stored = (store or get_object_store()).get(key)
    if stored is None:
        logger.error("document link %s: the logo %s is not in the object store", link.id, asset.id)
    return stored
