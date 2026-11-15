"""KAN-721: every `paths:` glob of a rule file matches at least one tracked
file. A rule loads only when a session reads a file its globs match, so a glob
naming files that were moved or deleted (KAN-701 deleted `utils/sortParam*`)
silently stops showing the ruling to the session that edits the code it
governs. Globs are read as Claude Code reads them (`scripts/rule_budget.py`:
`**` any number of directories, `*` and `?` within one)."""

import subprocess

from scripts import rule_budget


def _tracked_files() -> list[str]:
    """Committed files only: a glob matched by an uncommitted local file
    would pass here and fail on CI."""
    out = subprocess.run(
        ("git", "ls-files", "--cached"),
        cwd=rule_budget.ROOT, check=True, capture_output=True, text=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def dead_globs(rules: list[rule_budget.Rule], files: list[str]) -> list[str]:
    dead = []
    for rule in rules:
        for glob in rule.globs:
            pattern = rule_budget.glob_regex(glob)
            if not any(pattern.fullmatch(path) for path in files):
                dead.append(f"{rule.name}: {glob}")
    return dead


def test_a_glob_that_matches_no_file_is_reported():
    rule = rule_budget.Rule("x.md", 1, ("app/**", "frontend/apps/dms/src/utils/sortParam*"))

    assert dead_globs([rule], ["app/main.py", "frontend/apps/dms/src/hooks/useSortInUrl.ts"]) == [
        "x.md: frontend/apps/dms/src/utils/sortParam*"
    ]


def test_every_rule_file_glob_matches_a_tracked_file():
    dead = dead_globs(rule_budget.Tree(None).rules(), _tracked_files())

    assert dead == [], "rule-file globs that match no file (fix or remove them):\n" + "\n".join(dead)
