"""Checked-in seed data for platform-owned reference lists.

Currently just the ``country`` list (KAN-32). The CSV beside this module is
generated once by ``scripts/gen_country_reference_seed.py`` and committed;
loading it here is a pure in-process CSV read — no network, no Babel — the
same category of work as a hardcoded constant. Both the Alembic seed
migration and the test fixture that stands the list up call
``load_country_seed()`` so there is exactly one source of truth for the
rows.

**Frozen (KAN-517, ADR-133).** Two immutable migrations read this package —
`d7b1f4e02a96` the CSV, `e1f7a3c9b2d4` ``BUSINESS_LISTS`` — so an edit to
either would give a fresh database other rows than a deployed one.
`tests/architecture/test_migration_seed_sources_are_frozen.py` pins both by
digest. A change is a new data migration with literal rows (KAN-216's
`994244560c8a` is the pattern), never an edit here.
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


# --- Business master data lists (KAN-203) ---------------------------------------
#
# `legal_form` and `association_membership` are platform-seeded like `country`
# and, like it, hard-coded here so the Alembic seed migration and the tests
# share one source. The set is the Swiss one the Dealer Administration PRD
# names (AG, GmbH, Einzelfirma, Kollektivgesellschaft) extended by the two
# further forms a garage is commonly run as (Anto, 2026-10-24); a dealer
# never creates a list, platform_admin adds values on /settings/reference.

# (value_code, label_de, label_fr, label_it, label_en)
BusinessSeedRow = tuple[str, str, str, str, str]

LEGAL_FORM_LIST_CODE = "legal_form"
ASSOCIATION_MEMBERSHIP_LIST_CODE = "association_membership"

LEGAL_FORM_SEED: tuple[BusinessSeedRow, ...] = (
    ("ag", "Aktiengesellschaft (AG)", "Société anonyme (SA)", "Società anonima (SA)", "Public limited company (AG)"),
    (
        "gmbh",
        "Gesellschaft mit beschränkter Haftung (GmbH)",
        "Société à responsabilité limitée (Sàrl)",
        "Società a garanzia limitata (Sagl)",
        "Limited liability company (GmbH)",
    ),
    ("einzelfirma", "Einzelunternehmen", "Entreprise individuelle", "Impresa individuale", "Sole proprietorship"),
    (
        "kollektivgesellschaft",
        "Kollektivgesellschaft",
        "Société en nom collectif",
        "Società in nome collettivo",
        "General partnership",
    ),
    (
        "kommanditgesellschaft",
        "Kommanditgesellschaft",
        "Société en commandite",
        "Società in accomandita",
        "Limited partnership",
    ),
    ("genossenschaft", "Genossenschaft", "Société coopérative", "Società cooperativa", "Cooperative"),
)

ASSOCIATION_MEMBERSHIP_SEED: tuple[BusinessSeedRow, ...] = (
    ("agvs_upsa", "AGVS – Auto Gewerbe Verband Schweiz", "UPSA – Union professionnelle suisse de l'automobile", "UPSA – Unione professionale svizzera dell'automobile", "AGVS/UPSA – Swiss Automotive Trade Association"),
    ("auto_schweiz", "auto-schweiz", "auto-suisse", "auto-svizzera", "auto-schweiz"),
    ("swiss_automotive_aftermarket", "Swiss Automotive Aftermarket (SAA)", "Swiss Automotive Aftermarket (SAA)", "Swiss Automotive Aftermarket (SAA)", "Swiss Automotive Aftermarket (SAA)"),
)

BUSINESS_LISTS: dict[str, tuple[BusinessSeedRow, ...]] = {
    LEGAL_FORM_LIST_CODE: LEGAL_FORM_SEED,
    ASSOCIATION_MEMBERSHIP_LIST_CODE: ASSOCIATION_MEMBERSHIP_SEED,
}
