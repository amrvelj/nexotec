"""The settings store (KAN-805, ADR-214): a Betrieb's value of each declared
setting, an optional value per location, and every change.

`setting_value` holds one row per slot: the Betrieb's (`location_id` NULL)
and at most one per location. «Not set» is a state of its own (`state =
not_set`), not a missing row and never a default value: a cleared slot keeps
its row, its version and its history. A location's «not set» takes the
Betrieb's value (`setting_store.read_setting`).

The value is held in the column of its kind (`value_switch`,
`value_decimal` for a number or an amount, `value_choice`, `value_text` with
`value_text_translations`), so the database holds the shape and an amount stays
an exact `Numeric`. The key and kind come from the area's declaration
(`setting_declarations`); the kind is stored too, so a row never changes
meaning when a declaration does.

`setting_value_change` is append-only: one row per change — an edit, a
restore (Anto, 2026-11-24: restore kept for the settings store only; D-A-39 /
KAN-219 stay dropped elsewhere) or a copy from a sister Betrieb — with who
and when, the slot's state and value after it, and the version it made.
Every change is also an audit row (`audit.py`, unchanged).

Both reference `dealership` and `location`, which platform owns, so the
foreign keys are real.
"""

import datetime as dt
import enum
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.text_collation import IDENTIFIER_TEXT
from app.core.types import GUID, UTCDateTime
from app.db import Base
from app.platform.organisation.services.setting_declarations import SettingKind


class SettingState(enum.StrEnum):
    SET = "set"
    NOT_SET = "not_set"


class SettingChangeOrigin(enum.StrEnum):
    EDIT = "edit"
    RESTORE = "restore"
    COPY = "copy"


def _value_matches_kind() -> str:
    """No value while not set; exactly the kind's column while set (a text's
    translations may be absent)."""

    none = "value_switch IS NULL AND value_decimal IS NULL AND value_choice IS NULL AND value_text IS NULL"
    return (
        f"(state = 'not_set' AND {none} AND value_text_translations IS NULL) OR (state = 'set' AND ("
        "(kind = 'switch' AND value_switch IS NOT NULL AND value_decimal IS NULL AND value_choice IS NULL "
        "AND value_text IS NULL AND value_text_translations IS NULL) OR "
        "(kind IN ('number', 'amount') AND value_decimal IS NOT NULL AND value_switch IS NULL "
        "AND value_choice IS NULL AND value_text IS NULL AND value_text_translations IS NULL) OR "
        "(kind = 'choice' AND value_choice IS NOT NULL AND value_switch IS NULL AND value_decimal IS NULL "
        "AND value_text IS NULL AND value_text_translations IS NULL) OR "
        "(kind = 'text' AND value_text IS NOT NULL AND value_switch IS NULL AND value_decimal IS NULL "
        "AND value_choice IS NULL)))"
    )


class _ValueColumns:
    # A code (`<area>.<name>`), sorted byte-wise (ADR-190).
    key: Mapped[str] = mapped_column(String(64), nullable=False, info=IDENTIFIER_TEXT)
    kind: Mapped[SettingKind] = mapped_column(StoredEnum(SettingKind, length=16), nullable=False)
    state: Mapped[SettingState] = mapped_column(StoredEnum(SettingState, length=16), nullable=False)
    value_switch: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    value_decimal: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    value_choice: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # {"fr": …, "it": …} — only the languages translated (ruling 176).
    value_text_translations: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)


class SettingValue(PrimaryKeyMixin, VersionedMixin, TimestampMixin, _ValueColumns, Base):
    __tablename__ = "setting_value"
    __table_args__ = (
        Index(
            "uq_setting_value_dealership_key",
            "dealership_id",
            "key",
            unique=True,
            postgresql_where=text("location_id IS NULL"),
            sqlite_where=text("location_id IS NULL"),
        ),
        Index(
            "uq_setting_value_location_key",
            "location_id",
            "key",
            unique=True,
            postgresql_where=text("location_id IS NOT NULL"),
            sqlite_where=text("location_id IS NOT NULL"),
        ),
        CheckConstraint(_value_matches_kind(), name="ck_setting_value_value_matches_kind"),
    )

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False)
    location_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("location.id"), nullable=True)


class SettingValueChange(PrimaryKeyMixin, _ValueColumns, Base):
    __tablename__ = "setting_value_change"
    __table_args__ = (
        Index("ix_setting_value_change_slot_created", "setting_value_id", "created_at", "id"),
        Index("ix_setting_value_change_dealership_id", "dealership_id"),
        CheckConstraint(_value_matches_kind(), name="ck_setting_value_change_value_matches_kind"),
    )

    setting_value_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("setting_value.id"), nullable=False)
    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False)
    location_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("location.id"), nullable=True)
    origin: Mapped[SettingChangeOrigin] = mapped_column(StoredEnum(SettingChangeOrigin, length=16), nullable=False)
    # A restore names the change whose value it brought back.
    restored_from_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("setting_value_change.id"), nullable=True
    )
    version_after: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, comment="The user who changed it.")
