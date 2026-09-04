"""reference_list.label_de/fr/it/en — the list's own display name

The `/settings/reference` admin screen (FR-V-11) needs a localised name for
every canonical list in its picker; `reference_list` carried only the
snake_case `list_code`, which the i18n rule (DE/FR/IT/EN, no hardcoded
user-visible string) forbids rendering. Adds the four label columns and
seeds them for every list ever created by a migration:

  - WP-1 shell seed        (alembic c9654d846ac9)        —  8 lists
  - vehicle catalogue seed (alembic platform/6ba0a99ed5c4) — 16 lists

Safe against existing rows: columns land nullable, every known list is
seeded by `list_code`, any row not covered (there is none today) is
backfilled with its own `list_code` as a loud, obviously-untranslated
placeholder, and only then is NOT NULL enforced.

Revision ID: d4e9b1c7a052
Revises: 46c6382bdbf5
Create Date: 2026-09-04 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4e9b1c7a052'
down_revision: Union[str, Sequence[str], None] = '46c6382bdbf5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LABEL_COLUMNS = ("label_de", "label_fr", "label_it", "label_en")

# (list_code, label_de, label_fr, label_it, label_en)
LIST_LABELS: list[tuple[str, str, str, str, str]] = [
    # WP-1 shell seed (c9654d846ac9)
    ("vehicle_type", "Fahrzeugart", "Type de véhicule", "Tipo di veicolo", "Vehicle type"),
    ("fuel_type", "Treibstoff", "Carburant", "Carburante", "Fuel type"),
    ("body_style", "Karosserieform", "Type de carrosserie", "Tipo di carrozzeria", "Body style"),
    ("drivetrain", "Antriebsart", "Type de transmission", "Tipo di trazione", "Drivetrain"),
    ("transmission", "Getriebe", "Boîte de vitesses", "Cambio", "Transmission"),
    ("exterior_color", "Aussenfarbe", "Couleur extérieure", "Colore esterno", "Exterior colour"),
    ("interior_color", "Innenfarbe", "Couleur intérieure", "Colore interno", "Interior colour"),
    ("oem_affiliations", "Markenzugehörigkeit", "Affiliation de marque", "Affiliazione di marca", "Brand affiliation"),
    # vehicle catalogue seed (platform/6ba0a99ed5c4)
    ("vehicle_kind", "Fahrzeugkategorie", "Catégorie de véhicule", "Categoria di veicolo", "Vehicle kind"),
    ("vehicle_class", "Fahrzeugklasse", "Classe de véhicule", "Classe di veicolo", "Vehicle class"),
    ("colour", "Farbe", "Couleur", "Colore", "Colour"),
    ("colour_type", "Farbtyp", "Type de couleur", "Tipo di colore", "Colour type"),
    ("tyre_type", "Reifentyp", "Type de pneus", "Tipo di pneumatico", "Tyre type"),
    ("axle_position", "Achsposition", "Position de l'essieu", "Posizione dell'asse", "Axle position"),
    ("option_group", "Optionsgruppe", "Groupe d'options", "Gruppo di opzioni", "Option group"),
    ("equipment_feature", "Ausstattungsmerkmal", "Équipement", "Equipaggiamento", "Equipment feature"),
    ("accessory_type", "Zubehörart", "Type d'accessoire", "Tipo di accessorio", "Accessory type"),
    (
        "energy_efficiency_category",
        "Energieeffizienzkategorie",
        "Catégorie d'efficacité énergétique",
        "Categoria di efficienza energetica",
        "Energy efficiency category",
    ),
    ("emission_standard", "Abgasnorm", "Norme d'émission", "Norma sulle emissioni", "Emission standard"),
    ("consumption_norm", "Verbrauchsnorm", "Norme de consommation", "Norma di consumo", "Consumption norm"),
    (
        "registration_status",
        "Immatrikulationsstatus",
        "Statut d'immatriculation",
        "Stato di immatricolazione",
        "Registration status",
    ),
    ("vehicle_status", "Fahrzeugstatus", "Statut du véhicule", "Stato del veicolo", "Vehicle status"),
    (
        "custody_event_type",
        "Besitzereignistyp",
        "Type d'événement de possession",
        "Tipo di evento di custodia",
        "Custody event type",
    ),
    ("party_role", "Beteiligtenrolle", "Rôle de la partie", "Ruolo della parte", "Party role"),
]


def upgrade() -> None:
    for column in _LABEL_COLUMNS:
        op.add_column("reference_list", sa.Column(column, sa.String(length=200), nullable=True))

    reference_list = sa.table(
        "reference_list",
        sa.column("list_code", sa.String),
        sa.column("label_de", sa.String),
        sa.column("label_fr", sa.String),
        sa.column("label_it", sa.String),
        sa.column("label_en", sa.String),
    )

    # op.execute() (not bind.execute()) so this renders as literal-bind
    # static SQL under `alembic upgrade --sql` — same reason as
    # f2a6c1b7de3c_reference_value_label_en.py.
    for list_code, label_de, label_fr, label_it, label_en in LIST_LABELS:
        op.execute(
            reference_list.update()
            .where(reference_list.c.list_code == list_code)
            .values(label_de=label_de, label_fr=label_fr, label_it=label_it, label_en=label_en)
        )

    # Defensive: no row today is outside LIST_LABELS, but anything that
    # slipped through gets its own list_code as an obviously-untranslated
    # placeholder rather than blocking the NOT NULL below.
    for column in _LABEL_COLUMNS:
        op.execute(
            reference_list.update()
            .where(getattr(reference_list.c, column).is_(None))
            .values({column: reference_list.c.list_code})
        )

    for column in _LABEL_COLUMNS:
        op.alter_column("reference_list", column, nullable=False)


def downgrade() -> None:
    for column in reversed(_LABEL_COLUMNS):
        op.drop_column("reference_list", column)
