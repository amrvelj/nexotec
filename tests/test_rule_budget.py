"""KAN-614, KAN-615: the rule-file budget (scripts/rule_budget.py) - plain limits with no allowlist,
the scenario ceilings, and the `paths` glob matching the scenario report relies on."""

from scripts import rule_budget


def test_a_file_within_its_budget_passes_and_one_that_crosses_it_fails():
    assert rule_budget.problems({".claude/rules/a.md": 12_000}) == []
    [problem] = rule_budget.problems({".claude/rules/a.md": 12_001})
    assert "over its 12000-byte budget" in problem


def test_a_file_over_its_budget_fails_whatever_it_was_before():
    # KAN-615 cut the last files over budget, so the KAN-614 ratchet is gone: no exception.
    [problem] = rule_budget.problems({".claude/rules/sales.md": 12_500})
    assert "sales.md" in problem


def test_claude_md_has_its_own_budget():
    assert rule_budget.problems({"CLAUDE.md": 16_000}) == []
    assert rule_budget.problems({"CLAUDE.md": 16_001})


def test_the_two_files_that_load_almost_everywhere_stay_under_4000_bytes():
    for path in (".claude/rules/events.md", ".claude/rules/tests.md"):
        assert rule_budget.problems({path: 4_000}) == []
        [problem] = rule_budget.problems({path: 4_001})
        assert "over its 4000-byte budget" in problem


def test_a_scenario_over_its_limit_fails():
    assert rule_budget.scenario_problems({"full-stack sales ticket": 60_000, "one-file frontend fix": 15_000}) == []
    [problem] = rule_budget.scenario_problems({"full-stack sales ticket": 60_001, "one-file frontend fix": 1})
    assert "full-stack sales ticket" in problem
    [problem] = rule_budget.scenario_problems({"full-stack sales ticket": 1, "one-file frontend fix": 15_001})
    assert "one-file frontend fix" in problem


def test_a_scenario_file_that_no_longer_exists_fails_instead_of_counting_zero(monkeypatch):
    tree = rule_budget.Tree(None)
    assert rule_budget.missing_scenario_files(tree) == []
    monkeypatch.setitem(rule_budget.SCENARIOS, "one-file frontend fix", [("frontend/no/such/File.tsx",)])
    [problem] = rule_budget.missing_scenario_files(tree)
    assert "one-file frontend fix" in problem and "frontend/no/such/File.tsx" in problem


def test_the_rule_files_in_this_tree_are_within_every_budget():
    tree = rule_budget.Tree(None)
    assert rule_budget.problems(rule_budget.sizes(tree)) == []
    assert rule_budget.missing_scenario_files(tree) == []
    totals = {name: total for name, (total, _) in rule_budget.scenario_totals(tree).items()}
    assert rule_budget.scenario_problems(totals) == []


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

