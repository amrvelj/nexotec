"""KAN-617 — no source file grows past 1,000 lines.

`app/customer/services/customer.py` reached 4,208 lines (about 41,000 tokens)
before anything noticed: every customer ticket read it whole. A file that
size is a cost paid by every session that touches the area, and nothing
stopped it growing.

Every source file under `app/` and every `.ts`/`.tsx` file under a
frontend `src/` folder stays at or under `LIMIT` lines. Tests and generated
files are not counted (`schema.d.ts` is generated; a `*.test.*` / `*.spec.*`
file or anything under `__tests__/` is a test).

The files over the limit today are listed with their line count, each with
the follow-up ticket that splits it. The list is a ratchet, never a parking
place: a listed file that grows fails, a listed file that has come down to
the limit fails until its entry is removed, and an entry for a file that no
longer exists fails too. So the list only shrinks, and a listed file may be
trimmed (lower its count in the same change) but never extended.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

LIMIT = 1000

# repo-relative path -> (line count when listed, the ticket that splits it).
OVER_LIMIT: dict[str, tuple[int, str]] = {
    "app/platform/api/dealerships.py": (1076, "KAN-668"),
    "app/platform/services/user.py": (1071, "KAN-669"),
    "app/vehicle/services/identification.py": (1032, "KAN-670"),
    "frontend/apps/dms/src/components/configurator/ConfiguratorOverlay.tsx": (1459, "KAN-671"),
}

_GENERATED = {"schema.d.ts"}


def _is_test_or_generated(path: Path) -> bool:
    name = path.name
    if name in _GENERATED:
        return True
    if ".test." in name or ".spec." in name:
        return True
    return "__tests__" in path.parts


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
