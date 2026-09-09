"""Checked-in seed data for platform-owned reference lists.

Currently just the ``country`` list (KAN-32). The CSV beside this module is
generated once by ``scripts/gen_country_reference_seed.py`` and committed;
loading it here is a pure in-process CSV read — no network, no Babel — the
same category of work as a hardcoded constant. Both the Alembic seed
migration and the test fixture that stands the list up call
``load_country_seed()`` so there is exactly one source of truth for the
rows.
"""

from __future__ import annotations

import csv
from importlib import resources

# (value_code, label_de, label_fr, label_it, label_en)
CountrySeedRow = tuple[str, str, str, str, str]

_CSV_RESOURCE = "countries.csv"


def load_country_seed() -> list[CountrySeedRow]:
    """The ISO 3166-1 alpha-2 country rows, ordered by English name.

    ``value_code`` is the uppercase alpha-2 code (``CH``, ``HR``), matching
    the casing already used for ``customer_address.address_country`` and the
    Swiss-canton alphabet.
    """

    text = resources.files(__package__).joinpath(_CSV_RESOURCE).read_text(encoding="utf-8")
    rows: list[CountrySeedRow] = []
    reader = csv.reader(line for line in text.splitlines() if not line.startswith("#"))
    header = next(reader)
    if header != ["code", "label_de", "label_fr", "label_it", "label_en"]:
        raise ValueError(f"unexpected {_CSV_RESOURCE} header: {header!r}")
    for record in reader:
        code, label_de, label_fr, label_it, label_en = (field.strip() for field in record)
        if not all((code, label_de, label_fr, label_it, label_en)):
            raise ValueError(f"{_CSV_RESOURCE}: row for {code!r} is missing a label")
        rows.append((code.upper(), label_de, label_fr, label_it, label_en))
    return rows
