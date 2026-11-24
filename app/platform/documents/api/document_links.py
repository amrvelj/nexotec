"""The secure document link (KAN-217 PR-4; FR-A-52, ADR-193): what the
contact opens from an SMS or a WhatsApp message.

The message carries `{app}/d#<token>`: the token is in the URL's fragment,
which a browser never sends — so no proxy, access log, trace or referrer ever
sees it. The SPA's public page `/d` reads it and asks this route with the
token in the `X-Document-Link` header (a secret field by name,
`app/core/redact.py`).

Unauthenticated: the token is the whole authority, and it names its own
Betrieb (rule 7's tenant comes from the token — here the link's, never a
parameter). Every refusal is the same 404; the PDF is never cached by a proxy
or the browser and never indexed.
"""

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.schemas import CamelModel
from app.db import get_db
from app.platform.documents.services import document_links

router = APIRouter(tags=["document-links"])

# Every answer here is the contact's alone: never cached, never indexed, never
# a referrer.
_PRIVATE = {
    "Cache-Control": "no-store, private",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}


class DocumentLinkSenderRead(CamelModel):
    """KAN-217 PR-7 (ADR-208): whose page the contact is on."""

    name: str
    has_logo: bool


@router.get(
    "/document-links/current",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The linked document as a PDF."}},
)
def open_document_link(
    token: str = Header(default="", alias=document_links.TOKEN_HEADER),
    db: Session = Depends(get_db),
) -> Response:
    rendered = document_links.open_document_link(db, token)
    if rendered is None:
        raise NotFoundError("Not found.")
    filename = rendered.filename.replace('"', "")
    return Response(
        content=rendered.pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            # The SPA's page names the download after it; in development it
            # is another origin, so the header is exposed by name.
            "Access-Control-Expose-Headers": "Content-Disposition",
            **_PRIVATE,
        },
    )


@router.get("/document-links/current/sender", response_model=DocumentLinkSenderRead)
def read_document_link_sender(
    response: Response,
    token: str = Header(default="", alias=document_links.TOKEN_HEADER),
    db: Session = Depends(get_db),
) -> DocumentLinkSenderRead:
    """The Betrieb that sent the link: its name, and whether it has a logo
    (`…/current/logo`). The same 404 as the document for a link that names
    nobody (Anto, 2026-11-20, option 1)."""

    sender = document_links.read_link_sender(db, token)
    if sender is None:
        raise NotFoundError("Not found.")
    response.headers.update(_PRIVATE)
    return DocumentLinkSenderRead(name=sender.name, has_logo=sender.has_logo)


@router.get(
    "/document-links/current/logo",
    response_class=Response,
    responses={200: {"content": {"image/png": {}, "image/svg+xml": {}}, "description": "The sender's logo."}},
)
def open_document_link_logo(
    token: str = Header(default="", alias=document_links.TOKEN_HEADER),
    db: Session = Depends(get_db),
) -> Response:
    """The sending Betrieb's primary logo, as the Branding screen's own read
    serves it: an SVG is sandboxed, never sniffed."""

    stored = document_links.open_link_logo(db, token)
    if stored is None:
        raise NotFoundError("Not found.")
    return Response(
        content=stored.data,
        media_type=stored.content_type,
        headers={
            **_PRIVATE,
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        },
    )
