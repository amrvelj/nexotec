"""KAN-517: a migration's seed rows never move after it shipped.

A migration that reads its rows from a module the application keeps editing
gives a fresh database other rows than a deployed one — silently. New
migrations write their rows out literally (KAN-216's `994244560c8a` is the
pattern) and keep an equality test against the application's declaration.

Four migrations on `main` predate that rule and import their rows. They are
immutable (`.claude/rules/migrations.md`), so they cannot be rewritten with
literals; instead the exact data each one reads is frozen here by digest:

- `83d8634ff337` (KAN-204) — `reference_data_seed.holidays`: CALENDARS, HOLIDAYS, SOURCE
- `ec4559d84ad3` (KAN-208) — `services.standard_text_seed.SEED_TEXTS`
- `e1f7a3c9b2d4` (KAN-203) — `reference_data_seed.BUSINESS_LISTS`
- `d7b1f4e02a96` (KAN-32)  — `reference_data_seed.load_country_seed()` (`countries.csv`)

A digest that fails means one of those sources changed. Do not update the
digest: put the change in a **new** data migration with literal rows, and give
the application's new edition a name of its own — the name the old migration
imports stays bound to what it inserted (ADR-133).

The second test keeps the list closed: no other migration imports from a
bounded context; `app.core` (types, uuid7, utcnow) is fine.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from app.platform.reference_data_seed import BUSINESS_LISTS, load_country_seed
from app.platform.reference_data_seed.holidays import CALENDARS, HOLIDAYS, SOURCE
from app.platform.services.standard_text_seed import SEED_TEXTS

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"

# The migrations that read their seed from the application, and the modules
# they read. Immutable, so this list can never grow.
_IMPORTING_MIGRATIONS = {
    "platform/83d8634ff337_kan204_locations_holidays_closures.py": {"app.platform.reference_data_seed.holidays"},
    "platform/ec4559d84ad3_kan208_standard_texts.py": {"app.platform.services.standard_text_seed"},
    "platform/e1f7a3c9b2d4_kan203_business_master_data.py": {"app.platform.reference_data_seed"},
    "platform/d7b1f4e02a96_country_reference_list.py": {"app.platform.reference_data_seed"},
}

# What each migration inserted when it shipped (computed on main at ec48908,
# where every source still had the content of the commit that added it).
_FROZEN_DIGESTS = {
    "holidays": "1fcece46b9e38224164699cd9adff7f434288ceec81eb10211d3d3dd8ef340df",
    "standard_texts": "10411ae8878328dc3a11707f98f4785d858b78d5aa389531f20a021e3f3e767a",
    "business_lists": "71592deded7a711af0529d4388c5885cc6d496f303c23388b89a06885679b37c",
    "countries": "86c4bc262d46296b9ae5c60ec4ba361cd751c6c54f5184ff363e661392c35cf4",
}


def _digest(rows: object) -> str:
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def _holidays() -> object:
    """Every field `_seed_holiday_calendars` writes, in its order."""

    return {
        "source": SOURCE,
        "calendars": [
            [c.code, c.canton, c.label_de, c.label_fr, c.label_it, c.label_en, [list(h) for h in c.holidays]]
            for c in CALENDARS
        ],
        "holidays": {code: list(definition) for code, definition in HOLIDAYS.items()},
    }


def _standard_texts() -> object:
    """Every field the KAN-208 bulk insert takes from a seed, per language in its order."""

    return [
        [
            seed.code,
            seed.category.value,
            seed.applies_to.value,
            seed.slot.value,
            seed.sort_order,
            seed.is_default,
            [[language.value, seed.titles[language], seed.bodies[language]] for language in seed.titles],
        ]
        for seed in SEED_TEXTS
    ]


def _business_lists() -> object:
    return {code: [list(row) for row in rows] for code, rows in BUSINESS_LISTS.items()}


def _countries() -> object:
    return [list(row) for row in load_country_seed()]


_SOURCES = {
    "holidays": _holidays,
    "standard_texts": _standard_texts,
    "business_lists": _business_lists,
    "countries": _countries,
}


def test_the_rows_the_shipped_seed_migrations_read_are_frozen():
    changed = {name: _digest(source()) for name, source in _SOURCES.items()}
    changed = {name: digest for name, digest in changed.items() if digest != _FROZEN_DIGESTS[name]}
    assert not changed, (
        f"seed data an already-shipped migration reads has changed: {sorted(changed)}. A fresh database "
        "would get other rows than a deployed one. Revert the edit; put the change in a new data migration "
        "with literal rows and give the new edition its own name (KAN-517, ADR-133). Never update the digest."
    )


def _context_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        found.update(n for n in names if n.startswith("app.") and not n.startswith("app.core"))
    return found


def test_no_other_migration_reads_its_rows_from_a_bounded_context():
    offenders = {}
    for path in sorted(_VERSIONS.rglob("*.py")):
        relative = path.relative_to(_VERSIONS).as_posix()
        imports = _context_imports(path)
        if imports - _IMPORTING_MIGRATIONS.get(relative, set()):
            offenders[relative] = sorted(imports)
    assert not offenders, (
        "a migration imports from a bounded context; write its seed rows out literally and pin them to the "
        f"application's declaration with an equality test (KAN-216's 994244560c8a): {offenders}"
    )


def test_the_frozen_list_names_only_migrations_that_exist_and_still_import():
    for relative, modules in _IMPORTING_MIGRATIONS.items():
        path = _VERSIONS / relative
        assert path.is_file(), relative
        assert _context_imports(path) == modules, relative
