"""Vehicle catalogue's canonical reference lists (WP-5 PR-1, Vehicle PRD v0.6
phase V-A)

Seeds 16 new reference_list/reference_value rows. This lives on the
PLATFORM branch, not the vehicle branch, even though every one of these
lists exists for the vehicle catalogue's sake — reference_list/
reference_value is platform-owned data (CLAUDE.md's context table), and
this repo's per-context alembic branches give no ordering guarantee
against each other. The vehicle branch's own catalogue-tables migration
(74b6c2794698) originally carried this seed data inline and assumed
label_en already existed on reference_value by the time it ran — true
only if the platform branch's f2a6c1b7de3c (which adds that column) had
already applied, which alembic does not guarantee across independent
branches. The Postgres migration-smoke-test and outbox-worker-entrypoint
CI jobs caught this on PR #47 (column "label_en" does not exist) when
`upgrade heads` happened to apply the vehicle branch first. Split out here,
chained after the platform branch's actual head, so the column is
guaranteed to exist.

vehicle_type/fuel_type/body_style/drivetrain/transmission/exterior_color/
interior_color/oem_affiliations already exist from WP-1 and are reused
as-is.

Naming note: the PRD's canonical name for the vehicle-kind list is
`vehicle_kind` (passenger_car/light_commercial/motorcycle) — the SHIPPED
table's existing list is called `vehicle_type` with the same value codes.
This migration seeds `vehicle_kind` as a new, separate list under its PRD
name rather than renaming `vehicle_type` in place, because the old table
(and its own `vehicle_type` column) stays untouched and readable until PR-7
retires it. PR-7's migration maps `vehicle_type` values straight across to
`vehicle_kind` (same codes) when it splits old vehicle rows into VehicleMdm.

Four registration_status/vehicle_status/custody_event_type/party_role
lists are seeded even though the columns that use these codes stay
hardcoded Python enums (PR-3/PR-5/PR-9), not reference-value-backed
columns — matching this codebase's existing convention that lifecycle/
status enums stay hardcoded (app/platform/models/reference_data.py's own
docstring). The reference_value rows exist here purely so the SAME admin
screen (PR-8, ADR-044) is the one place that maintains every user-facing
label in this module, lifecycle enums included — not to make those
columns reference-value-driven.

Revision ID: 6ba0a99ed5c4
Revises: f3c2a7e6b1d4
Create Date: 2026-08-29 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = '6ba0a99ed5c4'
down_revision: Union[str, Sequence[str], None] = 'f3c2a7e6b1d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (list_code, [(value_code, label_de, label_fr, label_it, label_en), ...])
NEW_LISTS: list[tuple[str, list[tuple[str, str, str, str, str]]]] = [
    ("vehicle_kind", [
        ("passenger_car", "Personenwagen", "Voiture de tourisme", "Autovettura", "Passenger car"),
        ("light_commercial", "Leichtes Nutzfahrzeug", "Véhicule utilitaire léger", "Veicolo commerciale leggero", "Light commercial vehicle"),
        ("motorcycle", "Motorrad", "Motocycle", "Motociclo", "Motorcycle"),
    ]),
    ("vehicle_class", [
        ("m1", "M1 – Personenwagen", "M1 – Voiture de tourisme", "M1 – Autovettura", "M1 – Passenger car"),
        ("n1", "N1 – Leichtes Nutzfahrzeug", "N1 – Véhicule utilitaire léger", "N1 – Veicolo commerciale leggero", "N1 – Light commercial vehicle"),
        ("l3e", "L3e – Motorrad", "L3e – Motocycle", "L3e – Motociclo", "L3e – Motorcycle"),
        ("l1e", "L1e – Kleinkraftrad", "L1e – Cyclomoteur", "L1e – Ciclomotore", "L1e – Moped"),
    ]),
    ("colour", [
        ("black", "Schwarz", "Noir", "Nero", "Black"),
        ("white", "Weiss", "Blanc", "Bianco", "White"),
        ("grey", "Grau", "Gris", "Grigio", "Grey"),
        ("silver", "Silber", "Argent", "Argento", "Silver"),
        ("blue", "Blau", "Bleu", "Blu", "Blue"),
        ("red", "Rot", "Rouge", "Rosso", "Red"),
        ("green", "Grün", "Vert", "Verde", "Green"),
        ("brown", "Braun", "Brun", "Marrone", "Brown"),
    ]),
    ("colour_type", [
        ("solid", "Uni", "Uni", "Unito", "Solid"),
        ("metallic", "Metallic", "Métallisé", "Metallizzato", "Metallic"),
        ("pearl", "Perleffekt", "Nacré", "Perlato", "Pearl"),
        ("matte", "Matt", "Mat", "Opaco", "Matte"),
    ]),
    ("tyre_type", [
        ("summer", "Sommerreifen", "Pneus été", "Pneumatici estivi", "Summer tyres"),
        ("winter", "Winterreifen", "Pneus hiver", "Pneumatici invernali", "Winter tyres"),
        ("all_season", "Ganzjahresreifen", "Pneus toutes saisons", "Pneumatici quattro stagioni", "All-season tyres"),
    ]),
    ("axle_position", [
        ("front", "Vorne", "Avant", "Anteriore", "Front"),
        ("rear", "Hinten", "Arrière", "Posteriore", "Rear"),
    ]),
    ("option_group", [
        ("comfort", "Komfort", "Confort", "Comfort", "Comfort"),
        ("safety", "Sicherheit", "Sécurité", "Sicurezza", "Safety"),
        ("infotainment", "Infotainment", "Infodivertissement", "Infotainment", "Infotainment"),
        ("exterior", "Aussen", "Extérieur", "Esterno", "Exterior"),
        ("interior", "Innenraum", "Intérieur", "Interno", "Interior"),
        ("driver_assistance", "Fahrassistenz", "Aide à la conduite", "Assistenza alla guida", "Driver assistance"),
    ]),
    ("equipment_feature", [
        ("air_conditioning", "Klimaanlage", "Climatisation", "Climatizzatore", "Air conditioning"),
        ("navigation", "Navigationssystem", "Système de navigation", "Sistema di navigazione", "Navigation system"),
        ("parking_sensors", "Einparkhilfe", "Aide au stationnement", "Sensori di parcheggio", "Parking sensors"),
        ("leather_seats", "Ledersitze", "Sièges en cuir", "Sedili in pelle", "Leather seats"),
        ("sunroof", "Schiebedach", "Toit ouvrant", "Tetto apribile", "Sunroof"),
        ("cruise_control", "Tempomat", "Régulateur de vitesse", "Cruise control", "Cruise control"),
    ]),
    ("accessory_type", [
        ("towbar", "Anhängerkupplung", "Attelage", "Gancio traino", "Towbar"),
        ("roof_rack", "Dachträger", "Barres de toit", "Portapacchi", "Roof rack"),
        ("winter_wheels", "Winterräder", "Roues hiver", "Ruote invernali", "Winter wheels"),
        ("sound_system", "Soundsystem", "Système audio", "Impianto audio", "Sound system"),
        ("dash_cam", "Dashcam", "Caméra embarquée", "Dash cam", "Dash cam"),
        ("roof_box", "Dachbox", "Coffre de toit", "Box da tetto", "Roof box"),
    ]),
    ("energy_efficiency_category", [
        ("a", "A", "A", "A", "A"),
        ("b", "B", "B", "B", "B"),
        ("c", "C", "C", "C", "C"),
        ("d", "D", "D", "D", "D"),
        ("e", "E", "E", "E", "E"),
        ("f", "F", "F", "F", "F"),
        ("g", "G", "G", "G", "G"),
    ]),
    ("emission_standard", [
        ("euro_4", "Euro 4", "Euro 4", "Euro 4", "Euro 4"),
        ("euro_5", "Euro 5", "Euro 5", "Euro 5", "Euro 5"),
        ("euro_6", "Euro 6", "Euro 6", "Euro 6", "Euro 6"),
        ("euro_6d", "Euro 6d", "Euro 6d", "Euro 6d", "Euro 6d"),
    ]),
    ("consumption_norm", [
        ("nedc", "NEFZ", "NEDC", "NEDC", "NEDC"),
        ("wltp", "WLTP", "WLTP", "WLTP", "WLTP"),
    ]),
    ("registration_status", [
        ("unregistered", "Nicht immatrikuliert", "Non immatriculé", "Non immatricolato", "Unregistered"),
        ("registered", "Immatrikuliert", "Immatriculé", "Immatricolato", "Registered"),
        ("deregistered", "Deimmatrikuliert", "Désimmatriculé", "Radiato", "Deregistered"),
        ("export", "Export", "Export", "Esportazione", "Export"),
    ]),
    ("vehicle_status", [
        ("active", "Aktiv", "Actif", "Attivo", "Active"),
        ("exported", "Exportiert", "Exporté", "Esportato", "Exported"),
        ("scrapped", "Verschrottet", "Détruit", "Rottamato", "Scrapped"),
        ("stolen", "Gestohlen", "Volé", "Rubato", "Stolen"),
    ]),
    ("custody_event_type", [
        ("acquired", "Erworben", "Acquis", "Acquisito", "Acquired"),
        ("transferred", "Übertragen", "Transféré", "Trasferito", "Transferred"),
        ("sold", "Verkauft", "Vendu", "Venduto", "Sold"),
        ("repossessed", "Rückgenommen", "Repris", "Ripreso", "Repossessed"),
    ]),
    ("party_role", [
        ("owner", "Eigentümer", "Propriétaire", "Proprietario", "Owner"),
        ("keeper", "Halter", "Détenteur", "Detentore", "Keeper"),
        ("user", "Fahrer", "Conducteur", "Conducente", "User"),
    ]),
]


def upgrade() -> None:
    bind = op.get_bind()
    reference_list = sa.table(
        "reference_list",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("list_code", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    reference_value = sa.table(
        "reference_value",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("list_id", postgresql.UUID(as_uuid=True)),
        sa.column("value_code", sa.String()),
        sa.column("label_de", sa.String()),
        sa.column("label_fr", sa.String()),
        sa.column("label_it", sa.String()),
        sa.column("label_en", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("active", sa.Boolean()),
        sa.column("version", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    now = sa.func.now()
    for list_code, values in NEW_LISTS:
        list_id = uuid7()
        bind.execute(reference_list.insert().values(id=list_id, list_code=list_code, created_at=now, updated_at=now))
        for sort_order, (value_code, label_de, label_fr, label_it, label_en) in enumerate(values):
            bind.execute(
                reference_value.insert().values(
                    id=uuid7(),
                    list_id=list_id,
                    value_code=value_code,
                    label_de=label_de,
                    label_fr=label_fr,
                    label_it=label_it,
                    label_en=label_en,
                    sort_order=sort_order,
                    active=True,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    list_codes = tuple(list_code for list_code, _ in NEW_LISTS)
    bind.execute(
        sa.text(
            "DELETE FROM reference_value WHERE list_id IN "
            "(SELECT id FROM reference_list WHERE list_code IN :codes)"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": list_codes},
    )
    bind.execute(sa.text("DELETE FROM reference_list WHERE list_code IN :codes").bindparams(
        sa.bindparam("codes", expanding=True)
    ), {"codes": list_codes})
