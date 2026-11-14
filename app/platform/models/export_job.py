"""export_job (KAN-218; Dealer Administration D-A-31, FR-A-44/45).

One row per export of a Betrieb's data: requested, produced by the worker
into the object store as one ZIP, downloadable for seven days and then
deleted (Anto, 2026-11-13, Q1 A). Two kinds:

- `self_service` — the manager's own export, at any time (FR-A-44). The one
  the manager acknowledges: offboarding needs an acknowledged export produced
  after the Betrieb's last write (FR-A-45).
- `archive` — produced by platform staff from the ten-year archive for a
  former manager who asks Nexotec support (Q2 B: no dealer login after
  offboarding), with the requester and a reason, audited.

`requested_by` and `acknowledged_by` are user ids, plain GUIDs like every
actor column (a staff token's user is not necessarily a row here). The file
itself is never in the row — only its object-store key.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base


class ExportJobKind(str, enum.Enum):
    SELF_SERVICE = "self_service"
    ARCHIVE = "archive"


class ExportJobStatus(str, enum.Enum):
    REQUESTED = "requested"
    READY = "ready"
    FAILED = "failed"
    # The download window closed and the file was deleted (Q1 A).
    EXPIRED = "expired"


class ExportJob(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "export_job"
    __table_args__ = (Index("ix_export_job_dealership_id_created_at", "dealership_id", "created_at"),)

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False)
    kind: Mapped[ExportJobKind] = mapped_column(StoredEnum(ExportJobKind, length=16), nullable=False)
    status: Mapped[ExportJobStatus] = mapped_column(
        StoredEnum(ExportJobStatus, length=16), nullable=False, default=ExportJobStatus.REQUESTED
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, comment="The requesting user (platform).")
    # Staff's reason for an archive export (Q2 B); empty for the manager's own.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The moment the export's snapshot of the data was taken: everything
    # written before it is in the file.
    produced_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # KAN-716: what offboarding needs to tell a write the snapshot may not
    # see. `produced_xid_floor` (a 64-bit transaction id): the oldest id held
    # by a transaction of this database open just before the snapshot, else
    # the next id to be assigned — every transaction still open when the
    # snapshot began writes with an id at or above it. `produced_lookback_from`:
    # the earliest start of those transactions, less a margin for stamps taken
    # early and clock skew (`data_export.LOOKBACK_MARGIN`) — rows stamped
    # before it are old enough to leave out of the id test. A transaction
    # begun before the snapshot and committed after stamps its rows before
    # `produced_at`, yet they are not in the file; offboarding refuses on a row
    # stamped since the look-back whose writer id is at or above the floor.
    # NULL on SQLite; NULL on Postgres (an export produced before KAN-716)
    # makes offboarding call the export out of date.
    produced_xid_floor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    produced_lookback_from: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    download_expires_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    acknowledged_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="The manager who acknowledged it (platform user)."
    )
    expired_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # A stable code, never an exception's text.
    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
