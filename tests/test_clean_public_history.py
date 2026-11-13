"""KAN-682: the history inventory and the rewrite's push script.

The full rewrite needs `git filter-repo` and a mirror of the real repository; it was dry-run by hand
(see the ticket). What is pinned here: the push script leases every branch and tag on the tip the
clone saw and nothing else, a retired SOAP-era path is inventoried by path whatever it holds, and a
rewrite inside a checkout is refused before anything is written.
"""

import subprocess
from pathlib import Path

import pytest

from scripts import clean_public_history as chp

ROOT = Path(__file__).resolve().parents[1]


def test_the_push_script_leases_every_branch_and_tag_and_no_pull_ref():
    tips = {
        "refs/heads/main": "a" * 40,
        "refs/heads/kan-1-x": "b" * 40,
        "refs/tags/v1": "c" * 40,
        "refs/pull/7/head": "d" * 40,
    }
    script = chp.push_script(Path("/w/mirror.git"), "https://example.invalid/r.git", tips)
    command = script.splitlines()[-1]
    assert command.startswith("exec git -C '/w/mirror.git' push ")
    assert f"--force-with-lease=refs/heads/main:{'a' * 40}" in command
    assert f"--force-with-lease=refs/heads/kan-1-x:{'b' * 40}" in command
    assert f"--force-with-lease=refs/tags/v1:{'c' * 40}" in command
    assert "refs/pull" not in command
    assert " --force " not in command and "--mirror" not in command and "--no-verify" not in command
    assert command.endswith("'https://example.invalid/r.git' 'refs/heads/*:refs/heads/*' 'refs/tags/*:refs/tags/*'")


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_a_retired_soap_path_is_inventoried_by_path(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    retired = work / chp.REMOVED_PATHS[0]
    retired.parent.mkdir(parents=True)
    retired.write_text("nothing guarded here\n")
    (work / "other.py").write_text("x = 1\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "one")
    _git(tmp_path, "clone", "-q", "--mirror", str(work), "mirror.git")

    findings, reaching, messages = chp.inventory(tmp_path / "mirror.git")

    assert list(findings) == [chp.REMOVED_PATHS[0]]
    assert findings[chp.REMOVED_PATHS[0]].kinds == {"specification-derived file"}
    assert reaching == ["refs/heads/main"] and messages == []


def test_a_rewrite_inside_a_checkout_is_refused_before_anything_is_written(tmp_path):
    inside = ROOT / "tmp-kan682-refused"
    with pytest.raises(SystemExit, match="inside a checkout"):
        chp.rewrite(inside, "HEAD:x.py", "https://example.invalid/r.git", None)
    assert not inside.exists()
