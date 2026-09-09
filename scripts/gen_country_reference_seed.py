"""Regenerate ``app/platform/reference_data_seed/countries.csv`` (KAN-32).

This is a **one-off developer tool**, not a runtime or test dependency. The
application and the test suite read the checked-in CSV; nothing imports
``babel``. Run it only when the country set or its labels need refreshing::

    pip install -e ".[seed-tools]"
    python scripts/gen_country_reference_seed.py

Sourcing (decided on KAN-32, "Where the list comes from"):

* **Code set** — Google's Dataset Publishing Language canonical
  ``countries.csv`` (~245 rows, ISO 3166-1 alpha-2), licensed **CC BY 4.0**.
  Used here strictly as the cross-check on which codes exist, which is the
  role the ticket assigns it ("a cross-check on CLDR's territory set rather
  than the source of record").
* **Labels, all four languages** — CLDR via Babel
  (``Locale(lang).territories[code]``). ADR-044 tier 1 requires DE/FR/IT/EN
  and Google publishes only English. Taking *English* from CLDR too (a
  deliberate deviation from the ticket's "English name from Google" — see
  the PR description) keeps every label in a row current and mutually
  consistent: Google's file still ships "Swaziland" / "Macedonia [FYROM]" /
  "Congo [DRC]", which would read oddly beside CLDR's "Eswatini" /
  "Nordmazedonien". Every label is editable in the FR-V-11 admin screen, so
  a reviewer who prefers Google's exact English can restore it there or in
  the CSV without a deploy.

Divergences between the two sources are printed and folded in deliberately:

* Google-only codes that are **not** current ISO 3166-1 alpha-2 (``AN``
  Netherlands Antilles, dissolved 2010; ``GZ`` Gaza Strip, not an ISO code)
  are dropped.
* CLDR-only codes that **are** officially assigned country codes (``SS``
  South Sudan — independent 2011, absent from Google's older file — plus
  the 2010 Caribbean-Netherlands split ``BQ``/``CW``/``SX`` and
  ``AX``/``BL``/``MF``) are added, with their English name from CLDR.
* CLDR's non-country 2-letter entries (``EU``, ``ZZ``, ``QO``, the
  exceptional reservations ``AC``/``CP``/``DG``/``EA``/``IC``/``TA``, …) are
  excluded by an explicit deny-list.
"""

from __future__ import annotations

import csv
import io
import sys
import urllib.request
from pathlib import Path

from babel import Locale

# Pinned raw file in google/dspl (CC BY 4.0). Pass a local path as argv[1]
# to skip the download.
GOOGLE_COUNTRIES_CSV_URL = (
    "https://raw.githubusercontent.com/google/dspl/master/samples/google/canonical/countries.csv"
)

OUT_PATH = Path(__file__).resolve().parent.parent / "app" / "platform" / "reference_data_seed" / "countries.csv"

LANGS = ("de", "fr", "it", "en")

# Google codes that are not current ISO 3166-1 alpha-2 country codes.
DROP_FROM_GOOGLE = {"AN", "GZ"}

# CLDR 2-letter territory codes that are not officially assigned country
# codes: continent/region groupings, the "unknown" sentinel, and ISO
# 3166-1 exceptional reservations.
NON_COUNTRY_CLDR = {
    "EU", "EZ", "UN", "QO", "XA", "XB", "ZZ",
    "AC", "CP", "CQ", "DG", "EA", "IC", "TA",
}

ATTRIBUTION_HEADER = [
    "# Country reference list — seed data for the `country` ReferenceList (KAN-32, ADR-044 tier 1).",
    "#",
    "# Regenerate with: python scripts/gen_country_reference_seed.py",
    "# (see that script for the full sourcing rationale).",
    "#",
    "# CODE SET cross-checked against Google's Dataset Publishing Language canonical",
    "#   countries.csv — https://github.com/google/dspl/blob/master/samples/google/canonical/countries.csv",
    "#   © Google, licensed CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/).",
    "#   No changes to Google's data are distributed in this file; it is used only to",
    "#   determine which ISO 3166-1 alpha-2 codes exist. See THIRD_PARTY_NOTICES.md.",
    "#",
    "# LABELS (de, fr, it, en) from the Unicode CLDR via Babel — https://cldr.unicode.org/",
    "#   Unicode data files are distributed under the Unicode-3.0 licence.",
    "#",
    "# This file is checked in. The application and tests read it directly and never",
    "# call Babel or the network. Labels are editable in the FR-V-11 admin screen.",
]


def load_google_codes(source: str | None) -> dict[str, str]:
    if source:
        raw = Path(source).read_text(encoding="utf-8")
    else:
        with urllib.request.urlopen(GOOGLE_COUNTRIES_CSV_URL) as resp:  # noqa: S310 - pinned https host
            raw = resp.read().decode("utf-8")
    codes: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(raw)):
        code = row["country"].strip().upper()
        codes[code] = row["name"].strip()
    return codes


def build_rows(google: dict[str, str]) -> list[tuple[str, str, str, str, str]]:
    territories = {lang: Locale(lang).territories for lang in LANGS}

    selected: set[str] = set()
    for code in google:
        if code in DROP_FROM_GOOGLE:
            continue
        selected.add(code)

    cldr_only_added: list[str] = []
    for code in territories["en"]:
        if len(code) != 2 or not code.isalpha():
            continue
        if code in selected or code in NON_COUNTRY_CLDR:
            continue
        if all(code in territories[lang] for lang in LANGS):
            selected.add(code)
            cldr_only_added.append(code)

    missing_labels: list[str] = []
    rows: list[tuple[str, str, str, str, str]] = []
    for code in selected:
        labels = {lang: territories[lang].get(code, "") for lang in LANGS}
        if not all(labels.values()):
            missing_labels.append(code)
            continue
        rows.append((code, labels["de"], labels["fr"], labels["it"], labels["en"]))

    rows.sort(key=lambda r: r[4].casefold())

    print(f"google rows: {len(google)}  ->  seeded: {len(rows)}")
    print(f"dropped from google (not current ISO 3166-1): {sorted(DROP_FROM_GOOGLE & set(google))}")
    print(f"added from CLDR (absent from google): {sorted(cldr_only_added)}")
    if missing_labels:
        print(f"WARNING - skipped (missing a CLDR label somewhere): {sorted(missing_labels)}", file=sys.stderr)
    return rows


def main() -> None:
    source = sys.argv[1] if len(sys.argv) > 1 else None
    google = load_google_codes(source)
    rows = build_rows(google)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8", newline="") as fh:
        for line in ATTRIBUTION_HEADER:
            fh.write(line + "\n")
        writer = csv.writer(fh)
        writer.writerow(["code", "label_de", "label_fr", "label_it", "label_en"])
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {OUT_PATH.relative_to(OUT_PATH.parents[3])}")


if __name__ == "__main__":
    main()
