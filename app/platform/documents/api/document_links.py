"""The secure document link (KAN-217 PR-4; FR-A-52, ADR-193): what the
customer opens from an SMS or a WhatsApp message.

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
from app.db import get_db
from app.platform.documents.services import document_links

router = APIRouter(tags=["document-links"])


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
            "Cache-Control": "no-store, private",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )
