"""KAN-614: the rule-file budget (scripts/rule_budget.py) - a ratchet with no allowlist, and the
`paths` glob matching its scenario report relies on."""

from scripts import rule_budget


def test_a_file_within_its_budget_passes_and_one_that_crosses_it_fails():
    assert rule_budget.problems({".claude/rules/a.md": 12_000}, {".claude/rules/a.md": 11_000}) == []
    [problem] = rule_budget.problems({".claude/rules/a.md": 12_001}, {".claude/rules/a.md": 11_000})
    assert "over its 12000-byte budget" in problem


def test_a_new_file_over_its_budget_fails():
    [problem] = rule_budget.problems({".claude/rules/new.md": 12_500}, {})
    assert "new.md" in problem


def test_a_file_already_over_on_the_base_may_shrink_but_never_grow():
    assert rule_budget.problems({".claude/rules/sales.md": 31_000}, {".claude/rules/sales.md": 31_471}) == []
    assert rule_budget.problems({".claude/rules/sales.md": 31_471}, {".claude/rules/sales.md": 31_471}) == []
    [problem] = rule_budget.problems({".claude/rules/sales.md": 31_472}, {".claude/rules/sales.md": 31_471})
    assert "may only shrink" in problem


def test_claude_md_has_its_own_budget():
    assert rule_budget.problems({"CLAUDE.md": 16_000}, {}) == []
    assert rule_budget.problems({"CLAUDE.md": 16_001}, {})


def test_paths_globs_match_as_claude_code_reads_them():
    rule = rule_budget.Rule("x.md", 1, ("app/platform/**", "frontend/apps/dms/src/**/*ustomer*", "tests/test_user*.py"))
    assert rule.loads_for("app/platform/services/user.py")
    assert rule.loads_for("frontend/apps/dms/src/pages/CustomerDetailPage.tsx")
    assert rule.loads_for("frontend/apps/dms/src/components/customer-detail")
    assert rule.loads_for("tests/test_user_deactivation.py")
    assert not rule.loads_for("app/platformx/services/user.py")
    assert not rule.loads_for("tests/sub/test_user.py")
    assert rule_budget.Rule("always.md", 1, ()).loads_for("anything/at/all.py")


def test_frontmatter_globs_are_read_from_the_paths_list():
    text = '---\npaths:\n  - "app/vehicle/**"\n  - "tests/test_vehicle*.py"\n---\n# Vehicles\n'
    assert rule_budget.frontmatter_globs(text) == ("app/vehicle/**", "tests/test_vehicle*.py")
    assert rule_budget.frontmatter_globs("# no frontmatter\n") == ()

