"""The tenant column every planning table carries, child tables included
(ADR-221, T7): the export scope and the reconciliation then stay one clause
each, and a query never needs a join to know whose row it reads."""

import uuid

from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import GUID


class PlanningTenantMixin:
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (Dealership.id). No DB-level FK."
    )
