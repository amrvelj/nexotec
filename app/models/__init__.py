"""Import every model module here so Base.metadata sees all tables
(needed by tests' create_all() and by Alembic's autogenerate).
"""

from app.models.audit import AuditEvent  # noqa: F401
from app.models.idempotency import IdempotencyRecord  # noqa: F401

__all__ = ["AuditEvent", "IdempotencyRecord"]
