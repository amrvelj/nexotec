"""Locations as full master data, opening hours per department, closures and
the cantonal holiday calendars (KAN-204; Dealer Administration PRD ›
Locations, D-A-02, D-A-27, FR-A-03, A-RULE-1)

- `location` gains `code` (unique per Betrieb where set: a partial unique
  index), `location_types` (JSON list), contacts (`contact_person`, `phone`,
  `email`, `lead_email`), `geo_lat`/`geo_lng`, `holiday_calendar_id`, the
  overrides `letterhead_address_override` and `bank_account_override_id`,
  `is_active`/`valid_from`/`valid_to` and the empty `warehouse_ref`.
  `calendar_ref` is untouched (Aftersales fills it). No `vat_override`: it
  is open with Anto (ADR-057 makes `dealership.vat_rate` the one VAT figure).
- `location_opening_hours`: one row per (location, department, weekday) the
  site is open — stored per department always (D-A-27).
- `location_closure`: the Betrieb's own closures (from, to, reason).
- `holiday_calendar` + `holiday`: platform reference data, seeded here for
  all 26 cantons from app.platform.reference_data_seed.holidays (the same
  rows the tests use). **Source:** SECO, "Feiertage Schweiz" (Anhang, Auszug
  ArG/ArGV 1–5), table "Stand am 12.12.2024" —
  https://www.seco.admin.ch/dam/de/sd-web/x5LtHPJdNis5/Anhang-Feiertage-Schweiz-Auszug-ArG-ArGV1-5-SECO-SB-2024-DE.pdf
  — the cantonal holidays equal to Sundays (Art. 20a para. 1 ArG) and the
  federal 1 August, with the sub-cantonal exceptions it lists (Aargau by
  district, Fribourg by the commune's confession, Neuchâtel/Le Landeron,
  Solothurn/Bucheggberg) as calendars of their own: 34 calendars. The date
  rules the table leaves unstated (Näfelser Fahrt, Jeûne genevois, Lundi du
  Jeûne fédéral, Appenzell's St Stephen's Day) and their sources are in the
  seed module's docstring.

Backfill (pure SQL, renders offline):
- every existing location: `location_types` = [], `is_active` = true, and
  the calendar of its canton where the canton has exactly one;
- FR-A-03: every dealership without a location gets one from its registered
  seat — named after the trade name or legal name, no code (the dealer sets
  it once), the seat's phone and canton calendar — as `create_dealership`
  now does for a new Betrieb. These rows carry a v4 id from
  gen_random_uuid(), as KAN-198's and KAN-203's backfills did.

Downgrade drops the four tables and the new columns. The first locations
the backfill created stay (a location is never deleted, A-RULE-1; they are
ordinary rows on the previous schema).

Revision ID: 83d8634ff337
Revises: 7d763a542516
Create Date: 2026-10-31 14:26:25.763766

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op
from app.core.uuid7 import uuid7
from app.platform.reference_data_seed.holidays import CALENDARS, HOLIDAYS, SOURCE

# revision identifiers, used by Alembic.
revision: str = "83d8634ff337"
down_revision: str | Sequence[str] | None = "7d763a542516"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at this revision, in enum order (enum_check_condition's wording).
_HOLIDAY_RULES = ("fixed_date", "easter_offset", "naefelser_fahrt", "jeune_genevois", "federal_fast_monday")
_HOLIDAY_CONDITIONS = ("always", "only_on_monday", "not_on_tuesday_or_saturday")
_DEPARTMENTS = ("sales", "service", "parts")

_UUID = postgresql.UUID(as_uuid=True)
_LOCATION_CALENDAR_FK = "location_holiday_calendar_id_fkey"
_LOCATION_BANK_ACCOUNT_FK = "location_bank_account_override_id_fkey"

BACKFILL_LOCATIONS_SQL = """
UPDATE location
SET location_types = '[]',
    is_active = true,
    holiday_calendar_id = (SELECT c.id FROM holiday_calendar c WHERE c.code = location.address_canton)
WHERE is_active IS NULL
"""

# FR-A-03 for the Betriebe that exist today; idempotent.
FIRST_LOCATION_SQL = """
INSERT INTO location (
    id, tenant_id, name, address_street, address_house_number, address_postal_code, address_locality,
    address_canton, address_country, calendar_ref, location_types, phone, holiday_calendar_id, is_active,
    version, created_at, updated_at, created_by, updated_by
)
SELECT gen_random_uuid(), d.id, COALESCE(d.trade_name, d.legal_name), d.address_street, d.address_house_number,
       d.address_postal_code, d.address_locality, d.address_canton, d.address_country, NULL, '[]', d.phone,
       (SELECT c.id FROM holiday_calendar c WHERE c.code = d.address_canton), true, 1, now(), now(), NULL, NULL
FROM dealership d
WHERE NOT EXISTS (SELECT 1 FROM location l WHERE l.tenant_id = d.id)
"""


def _check(column: str, values: tuple[str, ...]) -> str:
    return f'"{column}" IN (' + ", ".join(f"'{v}'" for v in values) + ")"


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", _UUID, nullable=True),
        sa.Column("updated_by", _UUID, nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "holiday_calendar",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("canton", sa.String(length=2), nullable=False),
        sa.Column("label_de", sa.String(length=300), nullable=False),
        sa.Column("label_fr", sa.String(length=300), nullable=False),
        sa.Column("label_it", sa.String(length=300), nullable=False),
        sa.Column("label_en", sa.String(length=300), nullable=False),
        sa.Column("source", sa.String(length=500), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("code", name="uq_holiday_calendar_code"),
    )
    op.create_index("ix_holiday_calendar_canton", "holiday_calendar", ["canton"])

    op.create_table(
        "holiday",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("calendar_id", _UUID, sa.ForeignKey("holiday_calendar.id"), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("label_de", sa.String(length=200), nullable=False),
        sa.Column("label_fr", sa.String(length=200), nullable=False),
        sa.Column("label_it", sa.String(length=200), nullable=False),
        sa.Column("label_en", sa.String(length=200), nullable=False),
        sa.Column("rule", sa.String(length=32), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=True),
        sa.Column("day", sa.SmallInteger(), nullable=True),
        sa.Column("easter_offset_days", sa.SmallInteger(), nullable=True),
        sa.Column("condition", sa.String(length=32), nullable=False),
        sa.Column("afternoon_only", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("calendar_id", "code", name="uq_holiday_calendar_id_code"),
        sa.CheckConstraint(
            "(rule <> 'fixed_date') OR (month BETWEEN 1 AND 12 AND day BETWEEN 1 AND 31)",
            name="ck_holiday_fixed_date_has_day",
        ),
        sa.CheckConstraint(
            "(rule <> 'easter_offset') OR (easter_offset_days IS NOT NULL)", name="ck_holiday_easter_offset_has_days"
        ),
        sa.CheckConstraint(_check("rule", _HOLIDAY_RULES), name="ck_holiday_rule_enum"),
        sa.CheckConstraint(_check("condition", _HOLIDAY_CONDITIONS), name="ck_holiday_condition_enum"),
    )
    op.create_index("ix_holiday_calendar_id", "holiday", ["calendar_id"])

    op.create_table(
        "location_opening_hours",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("location_id", _UUID, sa.ForeignKey("location.id"), nullable=False),
        sa.Column("department", sa.String(length=16), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("opens_at", sa.Time(), nullable=False),
        sa.Column("closes_at", sa.Time(), nullable=False),
        sa.Column("second_opens_at", sa.Time(), nullable=True),
        sa.Column("second_closes_at", sa.Time(), nullable=True),
        sa.UniqueConstraint(
            "location_id", "department", "weekday", name="uq_location_opening_hours_location_department_weekday"
        ),
        sa.CheckConstraint("weekday BETWEEN 1 AND 7", name="ck_location_opening_hours_weekday"),
        sa.CheckConstraint("opens_at < closes_at", name="ck_location_opening_hours_interval"),
        sa.CheckConstraint(
            "(second_opens_at IS NULL AND second_closes_at IS NULL) OR "
            "(second_opens_at IS NOT NULL AND second_closes_at IS NOT NULL "
            "AND closes_at < second_opens_at AND second_opens_at < second_closes_at)",
            name="ck_location_opening_hours_second_interval",
        ),
        sa.CheckConstraint(_check("department", _DEPARTMENTS), name="ck_location_opening_hours_department_enum"),
    )
    op.create_index("ix_location_opening_hours_location_id", "location_opening_hours", ["location_id"])

    op.create_table(
        "location_closure",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("location_id", _UUID, sa.ForeignKey("location.id"), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint("starts_on <= ends_on", name="ck_location_closure_period"),
    )
    op.create_index("ix_location_closure_location_id", "location_closure", ["location_id"])

    op.add_column("location", sa.Column("code", sa.String(length=20), nullable=True))
    op.add_column("location", sa.Column("location_types", sa.JSON(), nullable=True))
    op.add_column("location", sa.Column("contact_person", sa.String(length=200), nullable=True))
    op.add_column("location", sa.Column("phone", sa.String(length=20), nullable=True))
    op.add_column("location", sa.Column("email", sa.String(length=254), nullable=True))
    op.add_column("location", sa.Column("lead_email", sa.String(length=254), nullable=True))
    op.add_column("location", sa.Column("geo_lat", sa.DECIMAL(precision=9, scale=6), nullable=True))
    op.add_column("location", sa.Column("geo_lng", sa.DECIMAL(precision=9, scale=6), nullable=True))
    op.add_column("location", sa.Column("holiday_calendar_id", _UUID, nullable=True))
    op.add_column("location", sa.Column("letterhead_address_override", sa.String(length=500), nullable=True))
    op.add_column("location", sa.Column("bank_account_override_id", _UUID, nullable=True))
    op.add_column("location", sa.Column("is_active", sa.Boolean(), nullable=True))
    op.add_column("location", sa.Column("valid_from", sa.Date(), nullable=True))
    op.add_column("location", sa.Column("valid_to", sa.Date(), nullable=True))
    op.add_column("location", sa.Column("warehouse_ref", sa.String(length=100), nullable=True))
    op.create_index("ix_location_holiday_calendar_id", "location", ["holiday_calendar_id"])
    op.create_index(
        "uq_location_tenant_id_code",
        "location",
        ["tenant_id", "code"],
        unique=True,
        postgresql_where=sa.text("code IS NOT NULL"),
    )
    op.create_foreign_key(_LOCATION_CALENDAR_FK, "location", "holiday_calendar", ["holiday_calendar_id"], ["id"])
    op.create_foreign_key(
        _LOCATION_BANK_ACCOUNT_FK, "location", "dealership_bank_account", ["bank_account_override_id"], ["id"]
    )

    _seed_holiday_calendars()

    op.execute(sa.text(BACKFILL_LOCATIONS_SQL))
    op.alter_column("location", "location_types", existing_type=sa.JSON(), nullable=False)
    op.alter_column("location", "is_active", existing_type=sa.Boolean(), nullable=False)
    op.execute(sa.text(FIRST_LOCATION_SQL))
    if not context.is_offline_mode():
        conn = op.get_bind()
        locations = conn.execute(sa.text("SELECT count(*) FROM location")).scalar_one()
        without = conn.execute(
            sa.text(
                "SELECT count(*) FROM dealership d WHERE NOT EXISTS "
                "(SELECT 1 FROM location l WHERE l.tenant_id = d.id AND l.is_active)"
            )
        ).scalar_one()
        calendars = conn.execute(sa.text("SELECT count(*) FROM holiday_calendar")).scalar_one()
        holidays = conn.execute(sa.text("SELECT count(*) FROM holiday")).scalar_one()
        print(
            f"KAN-204: {calendars} holiday calendar(s), {holidays} holiday rule(s); {locations} location(s), "
            f"{without} dealership(s) without an active location"
        )
    else:
        op.execute("-- KAN-204 counts skipped: they read rows and need a live database")


def _seed_holiday_calendars() -> None:
    bind = op.get_bind()
    calendar_table = sa.table(
        "holiday_calendar",
        *(
            sa.column(name)
            for name in (
                "id",
                "code",
                "canton",
                "label_de",
                "label_fr",
                "label_it",
                "label_en",
                "source",
                "sort_order",
                "active",
                "version",
                "created_at",
                "updated_at",
            )
        ),
    )
    holiday_table = sa.table(
        "holiday",
        *(
            sa.column(name)
            for name in (
                "id",
                "calendar_id",
                "code",
                "label_de",
                "label_fr",
                "label_it",
                "label_en",
                "rule",
                "month",
                "day",
                "easter_offset_days",
                "condition",
                "afternoon_only",
                "sort_order",
                "version",
                "created_at",
                "updated_at",
            )
        ),
    )
    now = sa.func.now()
    for calendar_order, calendar in enumerate(CALENDARS):
        calendar_id = uuid7()
        bind.execute(
            calendar_table.insert().values(
                id=calendar_id,
                code=calendar.code,
                canton=calendar.canton,
                label_de=calendar.label_de,
                label_fr=calendar.label_fr,
                label_it=calendar.label_it,
                label_en=calendar.label_en,
                source=SOURCE,
                sort_order=calendar_order,
                active=True,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        for holiday_order, entry in enumerate(calendar.holidays):
            definition = HOLIDAYS[entry.code]
            bind.execute(
                holiday_table.insert().values(
                    id=uuid7(),
                    calendar_id=calendar_id,
                    code=entry.code,
                    label_de=definition.label_de,
                    label_fr=definition.label_fr,
                    label_it=definition.label_it,
                    label_en=definition.label_en,
                    rule=definition.rule,
                    month=definition.month,
                    day=definition.day,
                    easter_offset_days=definition.easter_offset_days,
                    condition=entry.condition,
                    afternoon_only=entry.afternoon_only,
                    sort_order=holiday_order,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )


def downgrade() -> None:
    op.drop_constraint(_LOCATION_BANK_ACCOUNT_FK, "location", type_="foreignkey")
    op.drop_constraint(_LOCATION_CALENDAR_FK, "location", type_="foreignkey")
    op.drop_index("uq_location_tenant_id_code", table_name="location")
    op.drop_index("ix_location_holiday_calendar_id", table_name="location")
    for column in (
        "warehouse_ref",
        "valid_to",
        "valid_from",
        "is_active",
        "bank_account_override_id",
        "letterhead_address_override",
        "holiday_calendar_id",
        "geo_lng",
        "geo_lat",
        "lead_email",
        "email",
        "phone",
        "contact_person",
        "location_types",
        "code",
    ):
        op.drop_column("location", column)
    op.drop_index("ix_location_closure_location_id", table_name="location_closure")
    op.drop_table("location_closure")
    op.drop_index("ix_location_opening_hours_location_id", table_name="location_opening_hours")
    op.drop_table("location_opening_hours")
    op.drop_index("ix_holiday_calendar_id", table_name="holiday")
    op.drop_table("holiday")
    op.drop_index("ix_holiday_calendar_canton", table_name="holiday_calendar")
    op.drop_table("holiday_calendar")
