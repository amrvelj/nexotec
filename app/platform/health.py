from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness only — must stay a static reply. A liveness probe that
    touches the database conflates "is this process alive" with "can it
    reach its dependencies" (that's /readyz below), and Kubernetes/Render
    both restart the process on a failing liveness check — restarting a
    healthy process because the database had a blip is the outage this
    endpoint must never cause.
    """

    return {"status": "ok"}


@router.get("/readyz")
def readyz(db: Session = Depends(get_db)) -> JSONResponse:
    """Real dependency check (WP-2 PR-3, closes half of G-16) — the gap
    /healthz's static dict left: it returned "ok" while the database was
    unreachable, since it never checked anything. Deliberately outside the
    AppError taxonomy (preserve-verbatim, 400-422 only, no 5xx member) —
    same posture as /healthz already had: an infra probe, not a business
    endpoint, so a plain JSONResponse here rather than adding a status
    code the taxonomy was never meant to carry.
    """

    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — any DB failure means not ready, whatever the driver raises
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": str(exc)})
    return JSONResponse(status_code=200, content={"status": "ready"})
