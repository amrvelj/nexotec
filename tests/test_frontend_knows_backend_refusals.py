"""The SPA has a sentence for every refusal reason and blocking field the API names (KAN-679).

`frontend/apps/dms/src/utils/apiRefusal.test.ts` scans `app/` for the literal
refusal reasons and the marketplace blocking fields, and fails on one the SPA
cannot say. `scripts/dev/check` picks its lanes from the diff, and the
frontend lane runs only for a `frontend/` change: a backend-only PR that adds
a reason never ran that scan, merged green, and left `main` red (PR #316).
These are the same two scans in the backend lane, so the change that breaks
the rule is the one that fails. The vitest file keeps its own copy for the
frontend-side change (a sentence or a table entry removed).

The SPA's facts are read as files, never by running node: the reasons not
said on screen from `notARefusalReasons.json` (both tests read it), the
reasons the table knows from `REASON_KEYS`' source (the vitest file pins that
reading to `TRANSLATED_REFUSAL_REASONS`), the banner's fields from
`BLOCKING_FIELDS`, and the sentences from the four locale bundles.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.import_ui_translation_bundles import DEFAULT_BUNDLES_DIR, read_bundles

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
DMS_SRC = ROOT / "frontend" / "apps" / "dms" / "src"
API_REFUSAL_TS = DMS_SRC / "utils" / "apiRefusal.ts"
NOT_A_REFUSAL_JSON = DMS_SRC / "utils" / "notARefusalReasons.json"
BANNER_TSX = DMS_SRC / "modules" / "stock" / "components" / "stock-detail" / "BlockingConditionBanner.tsx"
LISTING_CONDITIONS = APP / "inventory" / "services" / "listing_conditions.py"

# The three literal forms apiRefusal.test.ts reads, verbatim (its REASON_LITERALS; a vitest case pins the two lists).
_REASON_LITERALS = (
    re.compile(r'"reason":\s*"([a-z0-9_]+)"'),
    re.compile(r'^(?:REFUSAL_REASON|COLLEAGUE_CANNOT_ACT_THERE)\s*=\s*"([a-z0-9_]+)"', re.MULTILINE),
    re.compile(r'reason: str = "([a-z0-9_]+)"'),
)


def raised_reasons(app_root: Path = APP) -> set[str]:
    """Every literal reason a module under `app_root` raises (or logs)."""

    raised: set[str] = set()
    for path in sorted(app_root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for pattern in _REASON_LITERALS:
            raised.update(pattern.findall(source))
    return raised


def translated_reasons() -> set[str]:
    """The reasons `REASON_KEYS` names: its `reason: 'key'` entries and its
    `'reason',` list items — the reading apiRefusal.test.ts pins."""

    source = API_REFUSAL_TS.read_text(encoding="utf-8")
    table = re.search(r"^const REASON_KEYS: Record<string, string> = \{\n(.*?)^\}\n", source, re.MULTILINE | re.DOTALL)
    assert table, "apiRefusal.ts no longer declares REASON_KEYS as this test reads it"
    named = re.findall(r"^\s*([a-z0-9_]+):|^\s*'([a-z0-9_]+)',?$", table.group(1), re.MULTILINE)
    return {key or item for key, item in named}


def not_a_refusal() -> set[str]:
    return set(json.loads(NOT_A_REFUSAL_JSON.read_text(encoding="utf-8")))


def untranslated(raised: set[str], translated: set[str], exempt: set[str]) -> list[str]:
    return sorted(raised - translated - exempt)


def test_the_scan_reads_the_backend_and_the_table():
    raised = raised_reasons()
    translated = translated_reasons()
    for reason in ("no_signed_contract", "plate_read_limit_reached", "last_manager"):
        assert reason in raised
        assert reason in translated


def test_every_listed_non_refusal_is_still_raised():
    raised = raised_reasons()
    assert sorted(not_a_refusal() - raised) == [], "no longer raised — drop it from notARefusalReasons.json"


def test_the_spa_translates_every_literal_reason_the_api_raises():
    assert untranslated(raised_reasons(), translated_reasons(), not_a_refusal()) == [], (
        "a refusal reason the SPA cannot say: add it to REASON_KEYS in frontend/apps/dms/src/utils/apiRefusal.ts "
        "with its sentence in all four bundles, or — if it is never a refusal body — to notARefusalReasons.json"
    )


def test_a_new_untranslated_reason_under_app_alone_fails(tmp_path):
    """The reproduction of KAN-679: a backend-only change raising a reason the SPA lacks."""

    module = tmp_path / "inventory" / "services" / "new_rule.py"
    module.parent.mkdir(parents=True)
    module.write_text('raise ConflictError("x", details={"reason": "kan_679_probe"})\n', encoding="utf-8")
    raised = raised_reasons() | raised_reasons(tmp_path)
    assert untranslated(raised, translated_reasons(), not_a_refusal()) == ["kan_679_probe"]


def test_the_blocking_condition_banner_knows_every_field_the_api_names():
    named = re.findall(r'BlockingCondition\(\s*field="([^"]+)"', LISTING_CONDITIONS.read_text(encoding="utf-8"))
    assert named
    banner = BANNER_TSX.read_text(encoding="utf-8")
    known = re.search(r"const BLOCKING_FIELDS = new Set\(\[([^\]]*)\]\)", banner)
    assert known, "BlockingConditionBanner.tsx no longer declares BLOCKING_FIELDS as this test reads it"
    assert [field for field in named if f"'{field}'" not in known.group(1)] == [], "a field BLOCKING_FIELDS lacks"
    missing = []
    for language, bundle in read_bundles(DEFAULT_BUNDLES_DIR).items():
        sentences = bundle.get("stockDetail", {}).get("publishing", {}).get("blockingCondition", {})
        missing += [f"{language.value}: {field}" for field in named if not isinstance(sentences.get(field), str)]
    assert missing == [], "a blocking field without its sentence in stockDetail.publishing.blockingCondition"
