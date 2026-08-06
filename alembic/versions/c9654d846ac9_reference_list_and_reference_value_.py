"""reference_list and reference_value tables + shell seed data

Revision ID: c9654d846ac9
Revises: 26099e1
Create Date: 2026-08-06 05:35:00.000000

"""
import datetime as dt
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = 'c9654d846ac9'
down_revision: Union[str, Sequence[str], None] = 'd176da890cdf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Seed lists for the shell (issue #3 scope) — a reasonable starter set per
# list so Vehicle creation isn't blocked on empty dropdowns. DE/FR/IT only,
# no English (Swiss addendum Round 2 Q3). Brand names in oem_affiliations
# are intentionally identical across the three labels — manufacturer names
# aren't translated.
SEED_LISTS: dict[str, list[tuple[str, str, str, str]]] = {
    "vehicle_type": [
        ("passenger_car", "Personenwagen", "Voiture de tourisme", "Autovettura"),
        ("light_commercial", "Lieferwagen", "Véhicule utilitaire léger", "Veicolo commerciale leggero"),
        ("truck", "Lastwagen", "Camion", "Autocarro"),
        ("motorcycle", "Motorrad", "Moto", "Motocicletta"),
    ],
    "fuel_type": [
        ("petrol", "Benzin", "Essence", "Benzina"),
        ("diesel", "Diesel", "Diesel", "Diesel"),
        ("electric", "Elektro", "Électrique", "Elettrico"),
        ("hybrid", "Hybrid", "Hybride", "Ibrido"),
        ("plugin_hybrid", "Plug-in-Hybrid", "Hybride rechargeable", "Ibrido plug-in"),
        ("hydrogen", "Wasserstoff", "Hydrogène", "Idrogeno"),
    ],
    "body_style": [
        ("sedan", "Limousine", "Berline", "Berlina"),
        ("hatchback", "Schrägheck", "Compacte à hayon", "Due volumi"),
        ("suv", "SUV", "SUV", "SUV"),
        ("estate", "Kombi", "Break", "Station wagon"),
        ("convertible", "Cabriolet", "Cabriolet", "Cabriolet"),
        ("coupe", "Coupé", "Coupé", "Coupé"),
        ("van", "Van", "Monospace", "Monovolume"),
        ("pickup", "Pick-up", "Pick-up", "Pick-up"),
    ],
    "drivetrain": [
        ("fwd", "Frontantrieb", "Traction avant", "Trazione anteriore"),
        ("rwd", "Heckantrieb", "Propulsion", "Trazione posteriore"),
        ("awd", "Allradantrieb", "Transmission intégrale", "Trazione integrale"),
    ],
    "transmission": [
        ("manual", "Schaltgetriebe", "Boîte manuelle", "Cambio manuale"),
        ("automatic", "Automatikgetriebe", "Boîte automatique", "Cambio automatico"),
        ("semi_automatic", "Halbautomatik", "Boîte semi-automatique", "Cambio semiautomatico"),
        ("cvt", "Stufenlosgetriebe (CVT)", "Boîte à variation continue (CVT)", "Cambio a variazione continua (CVT)"),
    ],
    "exterior_color": [
        ("black", "Schwarz", "Noir", "Nero"),
        ("white", "Weiss", "Blanc", "Bianco"),
        ("silver", "Silber", "Argent", "Argento"),
        ("grey", "Grau", "Gris", "Grigio"),
        ("blue", "Blau", "Bleu", "Blu"),
        ("red", "Rot", "Rouge", "Rosso"),
        ("green", "Grün", "Vert", "Verde"),
        ("brown", "Braun", "Marron", "Marrone"),
        ("beige", "Beige", "Beige", "Beige"),
        ("yellow", "Gelb", "Jaune", "Giallo"),
    ],
    "interior_color": [
        ("black", "Schwarz", "Noir", "Nero"),
        ("grey", "Grau", "Gris", "Grigio"),
        ("beige", "Beige", "Beige", "Beige"),
        ("brown", "Braun", "Marron", "Marrone"),
        ("red", "Rot", "Rouge", "Rosso"),
        ("white", "Weiss", "Blanc", "Bianco"),
    ],
    "oem_affiliations": [
        ("volkswagen", "Volkswagen", "Volkswagen", "Volkswagen"),
        ("bmw", "BMW", "BMW", "BMW"),
        ("mercedes_benz", "Mercedes-Benz", "Mercedes-Benz", "Mercedes-Benz"),
        ("audi", "Audi", "Audi", "Audi"),
        ("skoda", "Škoda", "Škoda", "Škoda"),
        ("renault", "Renault", "Renault", "Renault"),
        ("peugeot", "Peugeot", "Peugeot", "Peugeot"),
        ("fiat", "Fiat", "Fiat", "Fiat"),
        ("toyota", "Toyota", "Toyota", "Toyota"),
        ("ford", "Ford", "Ford", "Ford"),
    ],
}


def upgrade() -> None:
    op.create_table(
        "reference_list",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("list_code", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_reference_list_list_code", "reference_list", ["list_code"])
    op.create_unique_constraint("uq_reference_list_list_code", "reference_list", ["list_code"])

    op.create_table(
        "reference_value",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "list_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference_list.id", name="fk_reference_value_list_id_reference_list"),
            nullable=False,
        ),
        sa.Column("value_code", sa.String(length=64), nullable=False),
        sa.Column("label_de", sa.String(length=200), nullable=False),
        sa.Column("label_fr", sa.String(length=200), nullable=False),
        sa.Column("label_it", sa.String(length=200), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_reference_value_list_id", "reference_value", ["list_id"])
    op.create_unique_constraint(
        "uq_reference_value_list_id_value_code", "reference_value", ["list_id", "value_code"]
    )

    reference_list_table = sa.table(
        "reference_list",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("list_code", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    reference_value_table = sa.table(
        "reference_value",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("list_id", postgresql.UUID(as_uuid=True)),
        sa.column("value_code", sa.String),
        sa.column("label_de", sa.String),
        sa.column("label_fr", sa.String),
        sa.column("label_it", sa.String),
        sa.column("sort_order", sa.Integer),
        sa.column("active", sa.Boolean),
        sa.column("version", sa.Integer),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    now = dt.datetime.now(dt.timezone.utc)
    list_rows = []
    value_rows = []
    for list_code, values in SEED_LISTS.items():
        list_id = uuid7()
        list_rows.append({"id": list_id, "list_code": list_code, "created_at": now, "updated_at": now})
        for i, (value_code, label_de, label_fr, label_it) in enumerate(values):
            value_rows.append(
                {
                    "id": uuid7(),
                    "list_id": list_id,
                    "value_code": value_code,
                    "label_de": label_de,
                    "label_fr": label_fr,
                    "label_it": label_it,
                    "sort_order": (i + 1) * 10,
                    "active": True,
                    "version": 1,
                    "created_at": now,
                    "updated_at": now,
                }
            )

    op.bulk_insert(reference_list_table, list_rows)
    op.bulk_insert(reference_value_table, value_rows)


def downgrade() -> None:
    op.drop_constraint("uq_reference_value_list_id_value_code", "reference_value", type_="unique")
    op.drop_index("ix_reference_value_list_id", table_name="reference_value")
    op.drop_table("reference_value")
    op.drop_constraint("uq_reference_list_list_code", "reference_list", type_="unique")
    op.drop_index("ix_reference_list_list_code", table_name="reference_list")
    op.drop_table("reference_list")
