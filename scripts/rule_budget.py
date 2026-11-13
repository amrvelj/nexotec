"""What the rule files cost a session, and the budget that keeps them small (KAN-614, KAN-615).

Claude Code loads `CLAUDE.md` in every session and a `.claude/rules/*.md` file whenever the
session reads a file matching one of the rule's `paths` globs (a rule without `paths` loads
always). Every byte of them is paid again by each reviewer pass. The budget (.claude/README.md,
«What a rule file may contain»): a rule file at most `RULE_LIMIT` bytes, `CLAUDE.md` at most
`CLAUDE_LIMIT`, the two rule files that load almost everywhere at most their `OWN_LIMITS`, and two
of the scenarios below at most their `SCENARIO_LIMITS` in total.

    python3 scripts/rule_budget.py sizes             # every file's size against its limit
    python3 scripts/rule_budget.py check             # the config lane of scripts/dev/check: exit 1 when over budget
    python3 scripts/rule_budget.py scenarios         # which rule files three typical tickets load, origin/main vs now

`check` applies the plain limits, with no allowlist and no exception: a file over its limit fails,
whatever it was on the base. The base (`--base`, default the merge-base with origin/main) only
prints each file's change. Until KAN-615 cut the last files it was a ratchet; it no longer is.
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
# These two load for nearly every service file and every test (KAN-615, criterion 3).
OWN_LIMITS = {".claude/rules/events.md": 4_000, ".claude/rules/tests.md": 4_000}
# What a ticket of that shape may load in total (KAN-615, criterion 4).
SCENARIO_LIMITS = {"full-stack sales ticket": 60_000, "one-file frontend fix": 15_000}

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
    return CLAUDE_LIMIT if path == CLAUDE_MD else OWN_LIMITS.get(path, RULE_LIMIT)


def budgeted_files(tree: Tree) -> list[str]:
    return [CLAUDE_MD, *tree.rule_names()]


def sizes(tree: Tree) -> dict[str, int]:
    return {path: len(tree.read(path)) for path in budgeted_files(tree)}


def default_base() -> str | None:
    rc = subprocess.run(("git", "merge-base", "HEAD", "origin/main"), cwd=ROOT, capture_output=True, text=True,
                        check=False)
    return rc.stdout.strip() or None


def problems(now: dict[str, int]) -> list[str]:
    return [f"{path} is {size} bytes, over its {limit_for(path)}-byte budget - split it by sub-area"
            for path, size in now.items() if size > limit_for(path)]


def picker(tree: Tree) -> Callable[[tuple[str, ...]], str | None]:
    return lambda entry: next((p for p in entry if tree.exists(p)), None)


def scenario_totals(tree: Tree) -> dict[str, tuple[int, list[Rule]]]:
    rules, pick = tree.rules(), picker(tree)
    totals = {}
    for scenario, entries in SCENARIOS.items():
        read = [p for p in (pick(e) for e in entries) if p]
        loaded = [r for r in rules if any(r.loads_for(p) for p in read)]
        totals[scenario] = (sum(r.size for r in loaded), loaded)
    return totals


def missing_scenario_files(tree: Tree) -> list[str]:
    """A scenario entry none of whose paths exists would count 0 bytes and pass its ceiling vacuously
    (a file renamed or deleted): name it instead, so the scenario list follows the tree."""

    pick = picker(tree)
    return [f"the {scenario} names no existing file among {', '.join(entry)} - update SCENARIOS"
            for scenario, entries in SCENARIOS.items() for entry in entries if pick(entry) is None]


def scenario_problems(totals: dict[str, int]) -> list[str]:
    return [f"the {scenario} loads {totals[scenario]} bytes of rules, over its {limit} - narrow a glob or split "
            f"a file" for scenario, limit in SCENARIO_LIMITS.items() if totals.get(scenario, 0) > limit]


def print_sizes(now: dict[str, int], before: dict[str, int] | None = None) -> None:
    for path, size in now.items():
        limit = limit_for(path)
        mark = "ok  " if size <= limit else "OVER"
        delta = ""
        if before is not None and path in before and before[path] != size:
            delta = f"  ({size - before[path]:+d} vs base)"
        print(f"  {mark} {size:>7} / {limit:>6}  {path}{delta}")


def scenario_report(tree: Tree, label: str) -> dict[str, int]:
    count = len(tree.rule_names())
    print(f"{label}:")
    totals = {}
    for scenario, (total, loaded) in scenario_totals(tree).items():
        totals[scenario] = total
        names = ", ".join(Path(r.name).name for r in loaded) or "none"
        limit = f", limit {SCENARIO_LIMITS[scenario]:,}" if scenario in SCENARIO_LIMITS else ""
        print(f"  {scenario}: {len(loaded)} of {count} rule files, {total:,} bytes "
              f"(~{total // 4:,} tokens{limit}) - {names}")
    return totals


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sizes")
    check = sub.add_parser("check")
    check.add_argument("--base", help="the ref each size is compared with in the printout (default: merge-base with origin/main)")
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
        found = (problems(now) + scenario_problems(scenario_report(work, "scenarios (working tree)"))
                 + missing_scenario_files(work))
        for line in found:
            print(f"FAIL: {line}")
        return 1 if found else 0

    before = scenario_report(Tree(args.ref), f"before ({args.ref})")
    after = scenario_report(work, "after (working tree)")
    print("change:")
    for scenario in SCENARIOS:
        print(f"  {scenario}: {before[scenario]:,} -> {after[scenario]:,} bytes ({after[scenario] - before[scenario]:+,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
