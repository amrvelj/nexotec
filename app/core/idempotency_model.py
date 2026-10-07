import datetime as dt
import uuid
from typing import Any

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class IdempotencyRecord(Base):
    """One row per (tenant, idempotency key) on POST requests that opt in.

    request_hash guards against key reuse with a different payload (409,
    per the API-conventions error taxonomy) instead of silently returning a
    stale response for an unrelated request.

    response_status NULL is an in-flight claim (KAN-119): app/core/
    idempotent_route.py commits the row before the route runs and fills in
    the response once it succeeds, so a concurrent request with the same key
    finds the claim instead of doing the work a second time.
    """

    __tablename__ = "idempotency_record"

    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    request_path: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
