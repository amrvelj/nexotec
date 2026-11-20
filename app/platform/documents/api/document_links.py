"""The secure document link (KAN-217 PR-4; FR-A-52, ADR-192): what the
customer opens from an SMS or a WhatsApp message.

Unauthenticated: the token in the path is the whole authority, and it names
its own Betrieb (rule 7's tenant comes from the token — here the link's, never
a parameter). Every refusal is the same 404; the PDF is never cached by a
proxy or the browser, never indexed, and the page sends no referrer.
"""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db import get_db
from app.platform.documents.services import document_links

router = APIRouter(tags=["document-links"])


@router.get(
    "/document-links/{token}",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The linked document as a PDF."}},
)
def open_document_link(token: str, db: Session = Depends(get_db)) -> Response:
    rendered = document_links.open_document_link(db, token)
    if rendered is None:
        raise NotFoundError("Not found.")
    filename = rendered.filename.replace('"', "")
    return Response(
        content=rendered.pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "no-store, private",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )
