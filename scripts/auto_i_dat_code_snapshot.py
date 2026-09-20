"""Code-only snapshot of auto-i-dat's coded-field tables, and the decision recorded for every code the
seed leaves out (KAN-38, Configurator C-0 PR 2c).

Source: the "Kodierte Felder" tables on pp.32-34 of *Webservice Fahrzeuge* (footer date 22.06.2021), read
twice by different routes and reconciled — 19 numbered CodeGrpNr, **193 printed rows, 191 distinct
(CodeGrpNr, code) pairs**. Codes only: no provider label text is kept here or in the migration (the spec's
p1 notice forbids passing the document on). It is a 2021 snapshot; nothing in the spec says it equals the
live ``Codes`` result, which is exactly what ``scripts/verify_auto_i_dat.py`` is for once a staging account
exists.

Three consumers: ``tests/test_provider_code_map_seed.py`` (the partition and transcription checks),
``scripts/verify_auto_i_dat.py`` (the live diff), and a reader who wants to know *why* a code has no row.

The seeded side lives in the migration (``alembic/versions/vehicle/7c4e9a2b6d13_*``, frozen by design); this
side is the complement — every pair the seed does NOT map, with a category and a reason — so that
``seeded ∪ unmapped == SPEC_CODES`` is a mechanical check and a new code forces a decision rather than
silently going unmapped.
"""

# Categories of "not seeded" (the value is what the plan and the ADR call it).
NO_TARGET = "no_canonical_target"          # nothing in the canonical list means this
AMBIGUOUS = "ambiguous_business_decision"  # two defensible targets, or a judgement — an admin decides
NEEDS_CANONICAL = "needs_canonical_value"  # needs a new canonical value first (taxonomy / ADR)
ADAPTER_OWNS = "adapter_owns_it"           # the adapter hard-codes it; a map row would be a second definition

# Distinct codes per numbered CodeGrpNr, as printed on pp.32-34. Strings: codes are compared as printed.
SPEC_CODES: dict[str, tuple[str, ...]] = {
    "010": ("1", "3", "4", "5", "6", "8", "9"),
    "011": tuple(str(n) for n in range(1, 17)),
    "012": ("1", "2", "5"),
    "013": ("1", "2", "5", "6"),
    "014": tuple(str(n) for n in range(1, 7)),
    "020": ("1", "3", "4", "5", "7", "9", "10"),
    # 021 and 111 print code 14 twice and no code 15 (render and text layer): 16 printed rows, 15 distinct codes.
    "021": tuple(str(n) for n in (*range(1, 15), 16)),
    "022": ("1", "2", "5"),
    "023": ("1", "2", "5", "6"),
    "041": ("0", "1", "2", "4"),
    "045": tuple(str(n) for n in (*range(1, 58), *range(60, 71))),  # 58 and 59 are not printed
    "046": ("0", "1", "2", "3", "5", "7"),
    "047": tuple(str(n) for n in range(1, 7)),
    "065": ("0", "1", "2", "3", "4"),
    "110": ("1", "3", "4", "5", "7", "8", "9", "12", "13", "14"),
    "111": tuple(str(n) for n in (*range(1, 15), 16)),
    "112": ("2", "4", "9"),  # a stroke count: 2 Takt / 4 Takt / Kein Takt
    "500": ("121", "122"),
    "550": tuple(str(n) for n in range(1, 8)),
}

# Printed rows per group; differs from len(SPEC_CODES[g]) only for 021 and 111 (code 14 printed twice).
PRINTED_ROWS: dict[str, int] = {
    "010": 7, "011": 16, "012": 3, "013": 4, "014": 6, "020": 7, "021": 16, "022": 3, "023": 4, "041": 4,
    "045": 68, "046": 6, "047": 6, "065": 5, "110": 10, "111": 16, "112": 3, "500": 2, "550": 7,
}

TOTAL_PRINTED_ROWS = 193
TOTAL_DISTINCT_PAIRS = 191

_MAPPED_045 = {"1", "2", "3", "4", "5", "29", "31", "32", "43"}


def _build_unmapped() -> dict[tuple[str, str], tuple[str, str]]:
    unmapped: dict[tuple[str, str], tuple[str, str]] = {}

    def add(group: str, codes: "tuple[str, ...] | list[str] | set[str]", category: str, reason: str) -> None:
        for code in codes:
            unmapped[(group, code)] = (category, reason)

    add("014", SPEC_CODES["014"], NO_TARGET,
        "FzKlasse are size classes; the seeded vehicle_class list holds homologation classes (m1/n1/l3e/l1e)")
    add("110", SPEC_CODES["110"], NO_TARGET, "no canonical motorcycle body style exists")
    add("500", SPEC_CODES["500"], ADAPTER_OWNS,
        "the adapter hard-codes PneuTyp (auto_i_dat_soap.py); a map row would be a second definition")
    add("550", SPEC_CODES["550"], ADAPTER_OWNS,
        "the adapter hard-codes AchsenCode; codes 1 and 4-7 also lack a canonical axle_position value")

    ambiguous_045 = {"38", "47", "51"}
    add("045", [c for c in SPEC_CODES["045"] if c not in _MAPPED_045 and c not in ambiguous_045], NO_TARGET,
        "equipment_feature holds six values; there is no canonical value for this feature")
    add("045", sorted(ambiguous_045), AMBIGUOUS,
        "not a strict subset of the nearest canonical feature (fixed vs sliding roof; portable vs built-in "
        "navigation; partial vs full leather)")

    add("046", ("0", "1"), NO_TARGET, "not an option group (unassigned / a package marker)")
    add("046", ("7",), AMBIGUOUS, "communication vs infotainment is a taxonomy call")
    add("065", ("0", "1", "2"), NO_TARGET, "consumption_norm holds only nedc and wltp")

    add("010", ("8",), AMBIGUOUS, "no unique canonical body style (coupe / convertible / sedan)")
    add("020", ("3", "4", "9"), NO_TARGET, "no canonical body style for buses or cab-chassis")
    add("020", ("5", "10"), NEEDS_CANONICAL,
        "canonical 'van' reads Monospace / Monovolume in FR / IT (a people-carrier); a cargo-van value is "
        "needed first")

    for group in ("011", "021"):
        add(group, ("5", "6"), NO_TARGET, "bi-fuel gas / ethanol: no canonical fuel_type value")
    add("111", ("6",), NO_TARGET, "ethanol / petrol: no canonical fuel_type value")
    for group in ("011", "021", "111"):
        add(group, ("7", "8"), AMBIGUOUS,
            "unqualified electric + petrol/diesel: hybrid vs a range-extender EV is a business call")
        add(group, ("11", "14"), AMBIGUOUS,
            "mild hybrid: 'hybrid' vs the underlying fuel is a business decision (it changes filter behaviour)")

    for group in ("013", "023"):
        add(group, ("2",), AMBIGUOUS,
            "mechanically automated: the canonical semi_automatic is not documented as the same thing")
    return unmapped


# (CodeGrpNr, code) -> (category, reason) for every pair the seed deliberately does not map.
UNMAPPED: dict[tuple[str, str], tuple[str, str]] = _build_unmapped()
