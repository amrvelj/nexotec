"""dealership_contact_group (KAN-466; Anto, 2026-10-26).

Contact groups — Privat, Gewerbe, Flotte, Händler, Importeur, Versicherung,
Leasing, Behörde / öffentliche Hand — are a **Nexotec-defined** list: the
platform reference list `contact_group`, maintained by platform staff on the
Reference data screen like every reference list (FR-V-11; D-A-29 stands — no
dealer-invented fields). Each Betrieb switches on the ones it uses, in Dealer
Administration (A-RULE-0).

One row per (Betrieb, group) that is **on**; no row means off, and a new
Betrieb has none on. Switching a group off deletes its row (the audit log
keeps the history); the contacts that carry it keep it. `value_code` names a
`reference_value` of the `contact_group` list by its code, the way every
entity names a reference value; both sides are platform-owned, so the
dealership FK is a real one.
"""

import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin
from app.core.types import GUID
from app.db import Base

CONTACT_GROUP_LIST_CODE = "contact_group"


class DealershipContactGroup(PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "dealership_contact_group"
    __table_args__ = (
        UniqueConstraint("dealership_id", "value_code", name="uq_dealership_contact_group_dealership_id_value_code"),
    )

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False, index=True)
    value_code: Mapped[str] = mapped_column(String(64), nullable=False)
