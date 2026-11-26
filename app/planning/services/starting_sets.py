"""Nexotec's planner starting sets (KAN-808 PR-1d; ADR-222; Anto, 2026-11-25).

When a Betrieb first holds Aftersales, planning copies two sets into its S7
lists, so the manager starts from the Swiss workshop's usual titles and places
instead of an empty screen: the technician types and the workplace types
below. The constants are Nexotec's and change only with a new `EDITION`; once
copied an entry is the Betrieb's own row.

How an entry is written (ADR-222 (3)):

- `name` in the Betrieb's default correspondence language, the other three
  languages in `name_translations` (ruling 176's shape — DE, FR, IT, EN all
  first class);
- `seed_code`, the entry's stable identifier (`technician.apprentice`,
  `workplace.lift`, …), and `is_sample`: a sample until the manager changes or
  confirms it (`services/lists.py`);
- no rank and no counted-time factor: a figure is the Betrieb's own, never
  Nexotec's (ruling 174); the database refuses one on a sample;
- after the Betrieb's own entries, in the order below.

**Once per Betrieb, ever.** The copy takes the Betrieb's master-data lock — the
lock every list write takes — and then looks for the marker
(`planning_starting_set`): found, it copies nothing. So a redelivered fact, a
later re-grant, a restatement (`scripts/restate_module_entitlements.py`) or a
second consumer racing the first copies nothing again, and a sample the manager
deleted never returns. The marker's unique index backs the rule for any writer
that bypasses the lock.

**A name the Betrieb already uses** (case ignored, the lists' own rule) is left
alone: that seed entry is skipped, and the Betrieb's entry stays its own — never
turned into a sample, never given a seed code. The audit row names what was
skipped.

The caller commits (the consumer harness does). One audit row
`starting_set_copied` records the copy, its edition, the language, every entry
written and every entry skipped; the copy is the system's (no actor).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.i18n import SwissLanguage
from app.planning.models.starting_set import StartingSet
from app.planning.services.lists import TECHNICIAN_TYPES as TECHNICIAN_TYPE_LIST
from app.planning.services.lists import WORKPLACE_TYPES as WORKPLACE_TYPE_LIST
from app.planning.services.lists import ListSpec, name_taken
from app.planning.services.locks import lock_master_data
from app.platform.public import get_dealership_default_correspondence_language

EDITION = "2026-11"

DE, FR, IT, EN = SwissLanguage.DE, SwissLanguage.FR, SwissLanguage.IT, SwissLanguage.EN


@dataclass(frozen=True)
class SeedEntry:
    code: str
    names: Mapping[SwissLanguage, str]


def _entry(code: str, *, de: str, fr: str, it: str, en: str) -> SeedEntry:
    return SeedEntry(code=code, names={DE: de, FR: fr, IT: it, EN: en})


# Swiss vocational titles with their official FR and IT forms where they have one
# (EFZ/CFC/AFC and the federal diploma, without the suffix, as the DE names are);
# generic masculine, as Anto gave the DE names.
TECHNICIAN_TYPES: tuple[SeedEntry, ...] = (
    _entry("technician.apprentice", de="Lernender", fr="Apprenti", it="Apprendista", en="Apprentice"),
    _entry(
        "technician.maintenance_mechanic",
        de="Automobil-Fachmann",
        fr="Mécanicien en maintenance d'automobiles",
        it="Meccanico di manutenzione per automobili",
        en="Automotive maintenance technician",
    ),
    _entry(
        "technician.mechatronics_technician",
        de="Automobil-Mechatroniker",
        fr="Mécatronicien d'automobiles",
        it="Meccatronico d'automobili",
        en="Automotive mechatronics technician",
    ),
    _entry(
        "technician.diagnostician",
        de="Automobil-Diagnostiker",
        fr="Diagnosticien d'automobiles",
        it="Diagnostico d'automobili",
        en="Automotive diagnostician",
    ),
    _entry(
        "technician.workshop_manager",
        de="Werkstattchef",
        fr="Chef d'atelier",
        it="Capo officina",
        en="Workshop manager",
    ),
)

WORKPLACE_TYPES: tuple[SeedEntry, ...] = (
    _entry("workplace.lift", de="Hebebühne", fr="Pont élévateur", it="Ponte sollevatore", en="Lift"),
    _entry(
        "workplace.bay_without_lift",
        de="Arbeitsplatz ohne Hebebühne",
        fr="Poste de travail sans pont élévateur",
        it="Postazione di lavoro senza ponte sollevatore",
        en="Work bay without lift",
    ),
    _entry(
        "workplace.diagnosis",
        de="Diagnoseplatz",
        fr="Poste de diagnostic",
        it="Postazione di diagnosi",
        en="Diagnostic bay",
    ),
    _entry(
        "workplace.wheel_alignment",
        de="Achsvermessung",
        fr="Contrôle de la géométrie",
        it="Assetto ruote",
        en="Wheel alignment",
    ),
    _entry(
        "workplace.tyre_fitting",
        de="Reifenmontage",
        fr="Montage des pneus",
        it="Montaggio pneumatici",
        en="Tyre fitting",
    ),
    _entry("workplace.wash_bay", de="Waschplatz", fr="Place de lavage", it="Posto di lavaggio", en="Wash bay"),
)

_SETS: tuple[tuple[str, ListSpec, tuple[SeedEntry, ...]], ...] = (
    ("technicianTypes", TECHNICIAN_TYPE_LIST, TECHNICIAN_TYPES),
    ("workplaceTypes", WORKPLACE_TYPE_LIST, WORKPLACE_TYPES),
)


def copy_starting_sets(db: Session, *, dealership_id: uuid.UUID, source_event_id: uuid.UUID | None) -> bool:
    """Copies the starting sets into the Betrieb's lists, once ever. True when
    this call copied them, False when the marker says they were copied before.
    Adds and flushes; the caller commits."""

    lock_master_data(db, dealership_id)
    if db.scalar(select(StartingSet.id).where(StartingSet.tenant_id == dealership_id)) is not None:
        return False
    language = SwissLanguage(get_dealership_default_correspondence_language(db, dealership_id))

    written: dict[str, list[dict[str, Any]]] = {}
    skipped: dict[str, list[str]] = {}
    for key, spec, entries in _SETS:
        written[key], skipped[key] = _copy(db, spec, entries, dealership_id=dealership_id, language=language)

    marker = StartingSet(tenant_id=dealership_id, edition=EDITION, source_event_id=source_event_id)
    db.add(marker)
    db.flush()
    record_audit_event(
        db,
        entity_type="planning_starting_set",
        entity_id=marker.id,
        tenant_id=dealership_id,
        action="starting_set_copied",
        actor_id=None,
        after={
            "edition": EDITION,
            "language": language.value,
            "sourceEventId": str(source_event_id) if source_event_id is not None else None,
            **written,
            "skipped": skipped,
        },
        reason="ADR-222: Nexotec's planner starting sets, copied when the business first held Aftersales",
    )
    return True


def _copy(
    db: Session, spec: ListSpec, entries: tuple[SeedEntry, ...], *, dealership_id: uuid.UUID, language: SwissLanguage
) -> tuple[list[dict[str, Any]], list[str]]:
    model = spec.model
    last = db.scalar(select(func.max(model.sort_order)).where(model.tenant_id == dealership_id))
    sort_order = -1 if last is None else last
    written: list[dict[str, Any]] = []
    skipped: list[str] = []
    for entry in entries:
        name = entry.names[language]
        if name_taken(db, spec, dealership_id, name):
            skipped.append(entry.code)
            continue
        sort_order += 1
        row = model(
            tenant_id=dealership_id,
            name=name,
            name_translations={lang.value: text for lang, text in sorted(entry.names.items()) if lang != language},
            is_sample=True,
            seed_code=entry.code,
            sort_order=sort_order,
            created_by=None,
            updated_by=None,
        )
        db.add(row)
        db.flush()
        written.append({"id": str(row.id), "seedCode": entry.code, "name": name})
    return written, skipped
