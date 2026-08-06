"""Import every model module here so Base.metadata sees all tables
(needed by tests' create_all() and by Alembic's autogenerate).
"""

from app.models.audit import AuditEvent  # noqa: F401
from app.models.credential import Credential  # noqa: F401
from app.models.dealer import Dealer  # noqa: F401
from app.models.idempotency import IdempotencyRecord  # noqa: F401
from app.models.user import User  # noqa: F401

__all__ = ["AuditEvent", "Credential", "Dealer", "IdempotencyRecord", "User"]
