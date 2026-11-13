"""KAN-79: retire the legacy `vehicle` table and its custody-event table

Anto, 2026-11-02: no old-system sales history is imported at go-live, so the
legacy `vehicle` table (frozen for writes since ADR-021) and everything that
read it go now, without ADR-021's seven-clean-nights gate. The legacy-only
child `vehicle_custody_event` goes with it (`vehicle_mdm_custody_event` is the
live one and stays). ADR-091's import, the last reader, is deleted in the same
change.

**Data.** Dropped, deliberately: the rows had no other home
(`scripts/migrate_legacy_vehicles.py` carried what vehicle-mdm keeps) and the
ruling is that they are not needed. No other table holds a foreign key to
either (PR-2 dropped the cross-context ones); the one foreign key,
`vehicle_custody_event.vehicle_id -> vehicle.id`, goes with its table.

**Downgrade.** Recreates both tables empty, exactly as they stood at
`63bb7ea0e61c` (columns, defaults, CHECKs, unique constraint, indexes, the
inner foreign key, the column comments), literals only — the model is gone
(KAN-517) — so the older migrations' downgrades find every name they expect.
The data is not restored.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8d03887f7876"
down_revision: str | Sequence[str] | None = "63bb7ea0e61c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("vehicle_custody_event")
    op.drop_table("vehicle")


_CREATE_VEHICLE = """
CREATE TABLE vehicle (
    id uuid NOT NULL,
    vin character varying(17) NOT NULL,
    make character varying(100) NOT NULL,
    model character varying(100) NOT NULL,
    model_year integer NOT NULL,
    "trim" character varying(100),
    engine character varying(100),
    condition character varying(32) NOT NULL,
    vehicle_type character varying(64),
    fuel_type character varying(64),
    body_style character varying(64),
    drivetrain character varying(64),
    transmission character varying(64),
    exterior_color character varying(64),
    interior_color character varying(64),
    energy_efficiency_category character varying(4),
    co2_emissions_gkm integer,
    odometer integer,
    registration_status character varying(32) DEFAULT 'unregistered' NOT NULL,
    registration_canton character varying(2),
    status character varying(32) DEFAULT 'in_transit' NOT NULL,
    current_custodian_partner_id uuid,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    created_by uuid,
    updated_by uuid,
    CONSTRAINT vehicle_pkey PRIMARY KEY (id),
    CONSTRAINT uq_vehicle_vin UNIQUE (vin),
    CONSTRAINT ck_vehicle_condition_enum
        CHECK (condition IN ('new', 'used', 'certified_pre_owned', 'demo')),
    CONSTRAINT ck_vehicle_registration_status_enum
        CHECK (registration_status IN ('unregistered', 'registered', 'deregistered', 'export')),
    CONSTRAINT ck_vehicle_status_enum
        CHECK (status IN ('in_transit', 'in_stock', 'sold', 'in_service', 'totaled', 'scrapped'))
)
"""

_CREATE_CUSTODY_EVENT = """
CREATE TABLE vehicle_custody_event (
    id uuid NOT NULL,
    vehicle_id uuid NOT NULL,
    partner_id uuid NOT NULL,
    event_type character varying(32) NOT NULL,
    event_date timestamp with time zone NOT NULL,
    transaction_id uuid,
    created_at timestamp with time zone NOT NULL,
    created_by uuid,
    CONSTRAINT vehicle_custody_event_pkey PRIMARY KEY (id),
    CONSTRAINT fk_vehicle_custody_event_vehicle_id_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicle (id),
    CONSTRAINT ck_vehicle_custody_event_event_type_enum
        CHECK (event_type IN ('acquired', 'transferred', 'sold', 'repossessed'))
)
"""

_OWNER_COMMENT = "Owned by the {owner} context ({entity}). No DB-level FK (PR-2, ADR-015) — reconciled nightly."


def downgrade() -> None:
    op.execute(_CREATE_VEHICLE)
    op.create_index("ix_vehicle_vin", "vehicle", ["vin"])
    op.execute(_CREATE_CUSTODY_EVENT)
    op.create_index("ix_vehicle_custody_event_vehicle_id", "vehicle_custody_event", ["vehicle_id"])
    op.create_index("ix_vehicle_custody_event_partner_id", "vehicle_custody_event", ["partner_id"])
    op.create_index(
        "ix_vehicle_custody_event_vehicle_id_partner_id", "vehicle_custody_event", ["vehicle_id", "partner_id"]
    )
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite has no column comments.
    platform = _OWNER_COMMENT.format(owner="platform", entity="Dealership").replace("'", "''")
    sales = _OWNER_COMMENT.format(owner="sales", entity="Transaction").replace("'", "''")
    op.execute(f"COMMENT ON COLUMN vehicle.current_custodian_partner_id IS '{platform}'")
    op.execute(f"COMMENT ON COLUMN vehicle_custody_event.partner_id IS '{platform}'")
    op.execute(f"COMMENT ON COLUMN vehicle_custody_event.transaction_id IS '{sales}'")
