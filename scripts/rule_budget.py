"""What the rule files cost a session, and the budget that keeps them small (KAN-614).

Claude Code loads `CLAUDE.md` in every session and a `.claude/rules/*.md` file whenever the
session reads a file matching one of the rule's `paths` globs (a rule without `paths` loads
always). Every byte of them is paid again by each reviewer pass. The budget (.claude/README.md,
«What a rule file may contain»): a rule file at most `RULE_LIMIT` bytes, `CLAUDE.md` at most
`CLAUDE_LIMIT`.

    python3 scripts/rule_budget.py sizes             # every file's size against its limit
    python3 scripts/rule_budget.py check             # the config lane of scripts/dev/check: exit 1 when over budget
    python3 scripts/rule_budget.py scenarios         # which rule files three typical tickets load, origin/main vs now

`check` is a ratchet against the base (`--base`, default the merge-base with origin/main), with no
allowlist: a file over its limit fails unless it was already over on the base and has not grown.
Once every file is under its limit (KAN-615 cuts the rest), that is exactly the plain limit - a
file that crosses it fails, whatever it was before.
"""

import argparse
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = ".claude/rules"
CLAUDE_MD = "CLAUDE.md"
RULE_LIMIT = 12_000
CLAUDE_LIMIT = 16_000

# Three ticket shapes (epic KAN-609): the files such a session reads. Each entry lists alternatives;
# the first that exists at the ref counts, so a scenario survives a file moving (KAN-616's locales).
SCENARIOS: dict[str, list[tuple[str, ...]]] = {
    "sales backend ticket": [
        ("app/sales/services/offer.py",),
        ("app/sales/api/offers.py",),
        ("app/sales/public.py",),
        ("app/core/permissions.py",),
        ("app/main.py",),
        ("tests/test_sales_contract.py",),
        ("alembic/versions/sales/e7a3c5d1b9f4_kan570_deal_stock_item_label.py",),
    ],
    "full-stack sales ticket": [
        ("app/sales/services/offer.py",),
        ("app/sales/api/offers.py",),
        ("app/sales/public.py",),
        ("app/core/permissions.py",),
        ("app/main.py",),
        ("tests/test_sales_contract.py",),
        ("alembic/versions/sales/e7a3c5d1b9f4_kan570_deal_stock_item_label.py",),
        ("frontend/apps/dms/src/pages/OfferWorkspacePage.tsx",),
        ("frontend/apps/dms/src/components/deal-workspace/DealWorkspaceContainers.tsx",),
        ("frontend/apps/dms/src/pages/OfferWorkspace.saveQueue.render.test.tsx",),
        ("frontend/apps/dms/src/api/types.ts",),
        ("frontend/apps/dms/src/i18n/locales/de/offerWorkspace.json", "frontend/apps/dms/src/i18n/locales/de.json"),
        # The offer workspace's neighbours: the trade-in, the stock car, the configurator, the number,
        # the customer's tab - what a deal-workspace ticket opens on the way.
        ("app/sales/services/trade_in.py",),
        ("app/sales/services/numbering.py",),
        ("frontend/apps/dms/src/components/deal-workspace/StockNumberLink.tsx",),
        ("frontend/apps/dms/src/pages/OfferWorkspace.configurator.render.test.tsx",),
        ("frontend/apps/dms/src/components/customer-detail/OffersContractsTab.tsx",),
    ],
    "one-file frontend fix": [
        ("frontend/apps/dms/src/hooks/useDataGridLabels.ts",),
    ],
}


@dataclass(frozen=True)
class Rule:
    name: str
    size: int
    globs: tuple[str, ...]  # empty: loads in every session

    def loads_for(self, path: str) -> bool:
        return not self.globs or any(glob_regex(g).fullmatch(path) for g in self.globs)


def glob_regex(glob: str) -> re.Pattern[str]:
    """A rule's `paths` glob: `**` any number of directories, `*` and `?` within one."""
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif glob.startswith("**", i):
            out, i = out + ".*", i + 2
        elif glob[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif glob[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(glob[i]), i + 1
    return re.compile(out)


def frontmatter_globs(text: str) -> tuple[str, ...]:
    if not text.startswith("---\n"):
        return ()
    head = text[4:].split("\n---", 1)[0]
    return tuple(m.group(1) for m in re.finditer(r'^\s*-\s*"?([^"\n]+?)"?\s*$', head, re.MULTILINE))


class Tree:
    """The repository as committed at a ref, or the working tree (ref None)."""

    def __init__(self, ref: str | None):
        self.ref = ref
        self.files = set(self._git("ls-tree", "-r", "--name-only", ref).split("\n")) if ref else None

    @staticmethod
    def _git(*args: str) -> str:
        return subprocess.run(("git", *args), cwd=ROOT, check=True, capture_output=True, text=True).stdout

    def exists(self, path: str) -> bool:
        return path in self.files if self.files is not None else (ROOT / path).is_file()

    def read(self, path: str) -> bytes:
        if self.ref:
            return subprocess.run(("git", "show", f"{self.ref}:{path}"), cwd=ROOT, check=True,
                                  capture_output=True).stdout
        return (ROOT / path).read_bytes()

    def rule_names(self) -> list[str]:
        if self.files is not None:
            names = [f for f in self.files if f.startswith(RULES_DIR + "/")]
        else:
            names = [str(p.relative_to(ROOT)) for p in (ROOT / RULES_DIR).glob("*")]
        return sorted(n for n in names if n.endswith(".md"))

    def rules(self) -> list[Rule]:
        rules = []
        for name in self.rule_names():
            data = self.read(name)
            rules.append(Rule(name, len(data), frontmatter_globs(data.decode("utf-8"))))
        return rules


def limit_for(path: str) -> int:
    return CLAUDE_LIMIT if path == CLAUDE_MD else RULE_LIMIT


def budgeted_files(tree: Tree) -> list[str]:
    return [CLAUDE_MD, *tree.rule_names()]


def sizes(tree: Tree) -> dict[str, int]:
    return {path: len(tree.read(path)) for path in budgeted_files(tree)}


def default_base() -> str | None:
    rc = subprocess.run(("git", "merge-base", "HEAD", "origin/main"), cwd=ROOT, capture_output=True, text=True,
                        check=False)
    return rc.stdout.strip() or None


def problems(now: dict[str, int], before: dict[str, int]) -> list[str]:
    found = []
    for path, size in now.items():
        limit = limit_for(path)
        if size <= limit:
            continue
        was = before.get(path)
        if was is None or was <= limit:
            found.append(f"{path} is {size} bytes, over its {limit}-byte budget")
        elif size > was:
            found.append(f"{path} is {size} bytes, over its {limit}-byte budget and {size - was} bytes larger "
                         f"than on the base ({was}) - a file over budget may only shrink (KAN-615 cuts it)")
    return found


def print_sizes(now: dict[str, int], before: dict[str, int] | None = None) -> None:
    for path, size in now.items():
        limit = limit_for(path)
        mark = "ok  " if size <= limit else "OVER"
        delta = ""
        if before is not None and path in before and before[path] != size:
            delta = f"  ({size - before[path]:+d} vs base)"
        print(f"  {mark} {size:>7} / {limit:>6}  {path}{delta}")


def scenario_report(tree: Tree, label: str, pick: Callable[[tuple[str, ...]], str | None]) -> dict[str, int]:
    rules = tree.rules()
    print(f"{label}:")
    totals = {}
    for scenario, entries in SCENARIOS.items():
        read = [p for p in (pick(e) for e in entries) if p]
        loaded = [r for r in rules if any(r.loads_for(p) for p in read)]
        total = sum(r.size for r in loaded)
        totals[scenario] = total
        names = ", ".join(Path(r.name).name for r in loaded) or "none"
        print(f"  {scenario}: {len(loaded)} of {len(rules)} rule files, {total:,} bytes "
              f"(~{total // 4:,} tokens) - {names}")
    return totals


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sizes")
    check = sub.add_parser("check")
    check.add_argument("--base", help="the ref a file over budget is compared with (default: merge-base with origin/main)")
    scen = sub.add_parser("scenarios")
    scen.add_argument("--ref", default="origin/main", help="the 'before' ref (default origin/main)")
    args = parser.parse_args(argv)

    work = Tree(None)
    if args.command == "sizes":
        print_sizes(sizes(work))
        return 0
    if args.command == "check":
        base_ref = args.base or default_base()
        before = sizes(Tree(base_ref)) if base_ref else {}
        now = sizes(work)
        print(f"rule budget: rule files <= {RULE_LIMIT} bytes, {CLAUDE_MD} <= {CLAUDE_LIMIT} "
              f"(base {base_ref[:10] if base_ref else 'none'}):")
        print_sizes(now, before)
        found = problems(now, before)
        for line in found:
            print(f"FAIL: {line}")
        return 1 if found else 0

    def picker(tree: Tree) -> Callable[[tuple[str, ...]], str | None]:
        return lambda entry: next((p for p in entry if tree.exists(p)), None)

    old_tree = Tree(args.ref)
    before = scenario_report(old_tree, f"before ({args.ref})", picker(old_tree))
    after = scenario_report(work, "after (working tree)", picker(work))
    print("change:")
    for scenario in SCENARIOS:
        print(f"  {scenario}: {before[scenario]:,} -> {after[scenario]:,} bytes ({after[scenario] - before[scenario]:+,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
