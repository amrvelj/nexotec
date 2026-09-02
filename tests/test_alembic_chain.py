"""Alembic chain structure (PR-3, ADR-015): one branch per bounded context,
all forked from the same frozen trunk revision. Pure static analysis of
the migration files via ScriptDirectory — no database needed, so this runs
in both lanes. Guards against the chain structure being silently undone
(heads accidentally merged back together, a branch root deleted, a new
revision landing on the wrong parent) without anyone noticing until a
deploy silently skips a context.
"""

from alembic.config import Config
from alembic.script import ScriptDirectory

_TRUNK_HEAD = "b36486886126"  # last revision before the PR-3 split
_EXPECTED_BRANCH_LABELS = {
    "core", "platform", "customer", "vehicle", "sales", "inventory", "valuation", "integration",
}


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config("alembic.ini"))


def test_exactly_one_head_per_expected_context():
    script = _script_directory()
    heads = script.get_heads()

    labels_by_head = {head: script.get_revision(head).branch_labels for head in heads}
    all_labels = {label for labels in labels_by_head.values() for label in labels}

    assert all_labels == _EXPECTED_BRANCH_LABELS
    assert len(heads) == len(_EXPECTED_BRANCH_LABELS), (
        "expected exactly one head per context branch — a merge or a stray "
        f"revision changed the count: {labels_by_head}"
    )


def test_every_context_branch_forks_from_the_same_frozen_trunk_revision():
    script = _script_directory()

    for head in script.get_heads():
        revision = script.get_revision(head)
        # Walk down each branch's own ancestry until it reaches the trunk
        # head — true immediately for these five today (each is a direct
        # child of it), and stays true as a context's chain grows, since
        # the trunk head must still appear somewhere in that ancestry.
        current = revision
        seen = set()
        while current.revision != _TRUNK_HEAD:
            assert current.down_revision is not None, (
                f"{head} ({revision.branch_labels}) walked past <base> without "
                f"passing through the frozen trunk head {_TRUNK_HEAD}"
            )
            assert current.revision not in seen, f"cycle detected walking down from {head}"
            seen.add(current.revision)
            current = script.get_revision(current.down_revision)
