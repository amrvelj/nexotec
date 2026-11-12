"""KAN-617 — no source file grows past 1,000 lines.

`app/customer/services/customer.py` reached 4,208 lines (about 41,000 tokens)
before anything noticed: every customer ticket read it whole. A file that
size is a cost paid by every session that touches the area, and nothing
stopped it growing.

Every source file under `app/` and every `.ts`/`.tsx` file under a
frontend `src/` folder stays at or under `LIMIT` lines. Tests and generated
files are not counted (`schema.d.ts` is generated; a `*.test.*` / `*.spec.*`
file, anything under `__tests__/` and a test helper under a frontend
`src/test/` folder is a test).

The files over the limit today are listed with their line count, each with
the follow-up ticket that splits it. The list is a ratchet, never a parking
place: a listed file that grows fails, a listed file that has come down to
the limit fails until its entry is removed, and an entry for a file that no
longer exists fails too. So the list only shrinks, and a listed file may be
trimmed (lower its count in the same change) but never extended.

KAN-677 — «only shrinks» is asserted, not a convention: an entry must be one
of the four files listed on 2026-11-12 (`_FROZEN_BASELINE`), and that
baseline is pinned by its digest, so adding a path to both constants still
fails. A new oversized file is split; it cannot be listed.
"""

import hashlib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

LIMIT = 1000

# repo-relative path -> (line count when listed, the ticket that splits it).
OVER_LIMIT: dict[str, tuple[int, str]] = {}

# The list as it stood on 2026-11-12 (KAN-668 to KAN-671). It never gains a
# path: `test_the_frozen_baseline_is_unchanged` pins it by digest. It keeps a
# path whose entry has left OVER_LIMIT — the baseline is history, not a to-do.
_FROZEN_BASELINE: frozenset[str] = frozenset(
    {
        "app/platform/api/dealerships.py",
        "app/platform/services/user.py",
        "app/vehicle/services/identification.py",
        "frontend/apps/dms/src/components/configurator/ConfiguratorOverlay.tsx",
    }
)
_FROZEN_BASELINE_SHA256 = "757c6e0db9fd6fff94d6010dd1b77f453e8858adea6fe4e54c87a76b3b455b05"

_GENERATED = {"schema.d.ts"}


def _is_test_or_generated(path: Path) -> bool:
    name = path.name
    if name in _GENERATED:
        return True
    if ".test." in name or ".spec." in name:
        return True
    if "__tests__" in path.parts:
        return True
    return _is_frontend_test_helper(path)


def _is_frontend_test_helper(path: Path) -> bool:
    """A file under a frontend `src/test/` folder: fixtures and render helpers."""
    parts = path.parts
    return "frontend" in parts and any(parts[i] == "src" and parts[i + 1] == "test" for i in range(len(parts) - 1))


def _source_files() -> list[Path]:
    files = [p for p in (_REPO_ROOT / "app").rglob("*.py")]
    frontend = _REPO_ROOT / "frontend"
    for src in frontend.glob("**/src"):
        if "node_modules" in src.parts:
            continue
        for pattern in ("*.ts", "*.tsx"):
            files.extend(p for p in src.rglob(pattern) if "node_modules" not in p.parts)
    return sorted({p for p in files if not _is_test_or_generated(p)})


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_the_scan_sees_both_trees() -> None:
    files = {p.relative_to(_REPO_ROOT).as_posix() for p in _source_files()}
    assert "app/main.py" in files
    assert any(f.startswith("frontend/apps/dms/src/") and f.endswith(".tsx") for f in files)
    assert not any(f.endswith("schema.d.ts") or ".test." in f for f in files)


def test_frontend_test_helpers_are_not_counted_as_source() -> None:
    helper = _REPO_ROOT / "frontend/apps/dms/src/test/renderWithProviders.tsx"
    assert helper.exists(), "the helper this test names has moved — name another under src/test/"
    assert helper not in _source_files()
    assert _is_frontend_test_helper(helper)
    # `test` only counts directly under `src`, and only in the frontend.
    assert not _is_frontend_test_helper(_REPO_ROOT / "frontend/apps/dms/src/components/test/Thing.tsx")
    assert not _is_frontend_test_helper(_REPO_ROOT / "app/src/test/thing.py")


def test_no_entry_outside_the_frozen_baseline() -> None:
    added = sorted(set(OVER_LIMIT) - _FROZEN_BASELINE)
    assert not added, (
        f"OVER_LIMIT may only shrink (KAN-677): {added} was not on the list of 2026-11-12. "
        "Split the file along its seams instead of listing it."
    )


def test_the_frozen_baseline_is_unchanged() -> None:
    digest = hashlib.sha256("\n".join(sorted(_FROZEN_BASELINE)).encode()).hexdigest()
    assert digest == _FROZEN_BASELINE_SHA256, (
        "_FROZEN_BASELINE changed (KAN-677). It is the list of 2026-11-12 and never gains a path: "
        "an oversized file is split, not listed."
    )


def test_no_unlisted_source_file_exceeds_the_limit() -> None:
    too_long = {}
    for path in _source_files():
        rel = path.relative_to(_REPO_ROOT).as_posix()
        if rel in OVER_LIMIT:
            continue
        count = _line_count(path)
        if count > LIMIT:
            too_long[rel] = count
    assert not too_long, (
        f"source files over {LIMIT} lines — split them along their seams (KAN-617), do not list them: {too_long}"
    )


def test_listed_files_only_shrink() -> None:
    problems = []
    for rel, (listed, ticket) in sorted(OVER_LIMIT.items()):
        path = _REPO_ROOT / rel
        if not path.exists():
            problems.append(f"{rel} ({ticket}) no longer exists — remove its entry")
            continue
        count = _line_count(path)
        if count > listed:
            problems.append(f"{rel} grew from {listed} to {count} lines — it may only shrink ({ticket})")
        elif count <= LIMIT:
            problems.append(f"{rel} is down to {count} lines — remove its entry ({ticket})")
    assert not problems, "\n".join(problems)
