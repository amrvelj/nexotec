import datetime as dt

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import utcnow
from app.models.types import GUID, UTCDateTime


class IdempotencyRecord(Base):
    """One row per (tenant, idempotency key) on POST requests that opt in.

    request_hash guards against key reuse with a different payload (409,
    per the API-conventions error taxonomy) instead of silently returning a
    stale response for an unrelated request.
    """

    __tablename__ = "idempotency_record"

    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    tenant_id: Mapped[GUID] = mapped_column(GUID(), primary_key=True)
    request_path: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
