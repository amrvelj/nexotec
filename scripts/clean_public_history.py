"""Find, and remove from git history, what the identAPI test agreement keeps out of a public repository (KAN-682).

    python scripts/clean_public_history.py inventory <mirror.git>
    python scripts/clean_public_history.py rewrite   <workdir> --values-from <REV:PATH>

`inventory` scans every blob reachable from every ref of a mirror clone and prints, per path, the kinds
found there and the first and last commit holding one — never a value. It needs no plaintext: it looks
values up by the hashes in tests/architecture/test_no_real_provider_account_values.py, the same lookup
that test runs over the working tree, in every form it knows. Four kinds:

- account value — a value from the real account sheet (KAN-491);
- specification example — one of the specification's own example values;
- specification-derived sample XML — a result document of the retired SOAP «Webservice Fahrzeuge»
  service (KAN-550), recognised by its element names;
- specification-derived file — a retired SOAP-era file (`REMOVED_PATHS`) that described the
  specification's documents or encryption, whatever its content matches.

`rewrite` is the dry run and, once Anto has read its report, the input to his force-push. In
<workdir> (outside every checkout; refused otherwise) it clones a fresh mirror of `origin`, writes
the replacements file next to it, runs `git filter-repo` on the mirror, then runs `inventory` again
and checks that nothing is left, that `main`'s tree is unchanged, and which branch tips moved. It
never pushes: the force-push is Anto's, from his own machine (KAN-687), with the script it writes
(`push.sh`: one `--force-with-lease` per branch, so a branch that moved since the clone is refused,
not overwritten). A run with `--main-at` simulates `main` and writes no push script.

The plaintext for the replacements file comes from a guard test that still names the values,
`--values-from REV:PATH` read from this checkout's history (the ticket names it; this file does not).
It is written to <workdir>, never to a repository, read once by filter-repo and deleted. No bytecode
of it is written.

`refs/pull/*` are GitHub's: read-only, kept after any force-push, and they keep the old commits
reachable by SHA. Only GitHub Support can drop them; the report counts the ones that reach a finding.

Needs `git filter-repo` (`pip install git-filter-repo`, outside the project's environment).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "tests" / "architecture" / "test_no_real_provider_account_values.py"
ORIGIN = "https://github.com/amrvelj/nexotec.git"

# The SOAP service's result documents, by their element names (KAN-550 removed the last of them).
# (`\s`, so that this file does not match itself.)
SAMPLE_XML = re.compile(r"<(FzKey|TypSchNr|OptKey|KontrollschildInfo|Resultat)\b|spec Resultat\sXML")

# Retired SOAP-era files, none of them on `main`, removed from every commit: the adapter, the parser
# and its tests (sample result documents, an outline of the result document, the meaning of each
# status code with the specification's page references), the verify script, and the decryption helper
# (the specification's encryption scheme).
# Kept, by Anto's ruling of 2026-11-13: code-to-meaning tables are vocabulary, not covered by §12 -
# the code-map migration on `main`, the coded-field snapshot script, the code-map seed tests. Kept as
# looked at: the WSDL and envelope stubs of the timeout, WSDL-cache and failure-accounting tests - an
# invented `.invalid` namespace around the retired service's operation and parameter names, which
# the service itself serves in its WSDL.
REMOVED_PATHS = (
    "app/integration/adapters/aes_decrypt.py",
    "app/integration/adapters/auto_i_dat_parse.py",
    "app/integration/adapters/auto_i_dat_soap.py",
    "scripts/verify_auto_i_dat.py",
    "tests/test_integration_auto_i_dat_parse.py",
    "tests/test_integration_soap_adapter.py",
)

REPLACEMENT = "***REMOVED***"


def _load(source: str, file: Path) -> dict[str, Any]:
    """Run a guard test's source in a namespace of its own: no import, so no bytecode on disk."""
    namespace: dict[str, Any] = {"__file__": str(file), "__name__": "guard"}
    exec(compile(source, str(file), "exec"), namespace)  # noqa: S102 - this repository's own guard test, from its history
    return namespace


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True, check=True).stdout


@dataclass
class Finding:
    kinds: set[str] = field(default_factory=set)
    commits: list[tuple[str, str]] = field(default_factory=list)  # (ISO date, sha)


def _kinds_in(text: str, guard: dict[str, Any]) -> set[str]:
    kinds = set()
    if guard["guarded_hits"](text, guard["REAL_ACCOUNT_VALUE_HASHES"]):
        kinds.add("account value")
    if guard["guarded_hits"](text, guard["SPECIFICATION_EXAMPLE_HASHES"]):
        kinds.add("specification example")
    if SAMPLE_XML.search(text):
        kinds.add("specification-derived sample XML")
    return kinds


def _blobs(mirror: Path) -> list[str]:
    objects = _git(mirror, "rev-list", "--all", "--objects").decode("utf-8", "replace").splitlines()
    shas = [line.split(" ", 1)[0] for line in objects if " " in line]
    checks = _git(
        mirror, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)", stdin="\n".join(shas).encode()
    )
    return [
        sha
        for sha, kind, size in (line.split() for line in checks.decode().splitlines())
        if kind == "blob" and int(size) < 5_000_000
    ]


def inventory(mirror: Path) -> tuple[dict[str, Finding], list[str], list[str]]:
    """(findings per path, refs reaching a finding, commit messages holding a value)."""
    guard = _load(GUARD.read_text(encoding="utf-8"), GUARD)
    hit_blobs: dict[str, set[str]] = {}
    out = _git(mirror, "cat-file", "--batch", stdin="\n".join(_blobs(mirror)).encode())
    pos = 0
    while pos < len(out):
        header_end = out.index(b"\n", pos)
        sha, _, size_text = out[pos:header_end].decode().split()
        size = int(size_text)
        data = out[header_end + 1 : header_end + 1 + size]
        pos = header_end + 1 + size + 1
        if b"\x00" in data[:8000]:
            continue
        kinds = _kinds_in(data.decode("utf-8", "replace"), guard)
        if kinds:
            hit_blobs[sha] = kinds

    findings: dict[str, Finding] = defaultdict(Finding)
    hit_commits: set[str] = set()
    messages: list[str] = []
    for line in _git(mirror, "log", "--all", "--format=%H %cI").decode().splitlines():
        commit, date = line.split()
        for entry in _git(mirror, "ls-tree", "-r", commit).decode("utf-8", "replace").splitlines():
            meta, path = entry.split("\t", 1)
            blob = meta.split()[2]
            kinds = set(hit_blobs.get(blob, ()))
            if path in REMOVED_PATHS:
                kinds.add("specification-derived file")
            if kinds:
                findings[path].kinds |= kinds
                findings[path].commits.append((date, commit))
                hit_commits.add(commit)
        body = _git(mirror, "log", "-1", "--format=%B", commit).decode("utf-8", "replace")
        if _kinds_in(body, guard) - {"specification-derived sample XML"}:
            messages.append(commit)

    reaching = []
    if hit_commits:
        for ref in _git(mirror, "for-each-ref", "--format=%(refname)").decode().splitlines():
            if set(_git(mirror, "rev-list", ref).decode().split()) & hit_commits:
                reaching.append(ref)
    return dict(findings), reaching, messages


def _report(findings: dict[str, Finding], reaching: list[str], messages: list[str]) -> list[int]:
    for path, finding in sorted(findings.items()):
        commits = sorted(finding.commits)
        print(
            f"{path}\t{', '.join(sorted(finding.kinds))}\t"
            f"first {commits[0][1][:9]} ({commits[0][0][:10]})\tlast {commits[-1][1][:9]} ({commits[-1][0][:10]})\t"
            f"{len(commits)} commits"
        )
    heads = [r for r in reaching if not r.startswith("refs/pull/")]
    pulls = sorted({_pr(r) for r in reaching if r.startswith("refs/pull/")})
    print(f"paths with a finding: {len(findings)}")
    print(f"commit messages with a value: {len(messages)}")
    print(f"branches and tags reaching a finding: {len(heads)}")
    print(f"pull requests whose refs/pull/* reach a finding: {len(pulls)}" + (f" — {_ranges(pulls)}" if pulls else ""))
    return pulls


def _ranges(numbers: list[int]) -> str:
    """#3, #5-#9 style, for the GitHub Support request."""
    spans: list[list[int]] = []
    for n in numbers:
        if spans and n == spans[-1][1] + 1:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    return ", ".join(f"#{a}" if a == b else f"#{a}-#{b}" for a, b in spans)


def _pr(ref: str) -> int:
    return int(ref.split("/")[2])


def _replacements(values_from: str, out: Path) -> int:
    revision, path = values_from.split(":", 1)
    source = _git(ROOT, "show", f"{revision}:{path}").decode("utf-8")
    guard = _load(source, ROOT / path)
    values = [*guard["REAL_ACCOUNT_VALUES"], *guard["SPEC_EXAMPLES_IN_THE_MOCK"]]
    lines = []
    for value in values:
        # Any case, and inside a snake_case name too: the forms the guard test looks for.
        lines.append(rf"regex:(?i)(?<![0-9A-Za-z]){re.escape(value)}(?![0-9A-Za-z])==>{REPLACEMENT}")
        for cut in range(1, len(value)):
            head, tail = re.escape(value[:cut]), re.escape(value[cut:])
            lines.append(
                r"""regex:(["'])""" + head + r"\1\s*\+\s*" + r"""(["'])""" + tail + r"\2==>" + f'"{REPLACEMENT}"'
            )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(values)


def push_script(mirror: Path, origin: str, before_tips: dict[str, str]) -> str:
    """Anto's force-push: every branch and tag, each leased on the tip the clone saw."""
    leases = [
        f"--force-with-lease={ref}:{sha}"
        for ref, sha in sorted(before_tips.items())
        if ref.startswith(("refs/heads/", "refs/tags/"))
    ]
    return (
        "#!/bin/sh\n"
        "# KAN-682: Anto's force-push, after the order on KAN-687. A branch or tag that moved since the clone\n"
        "# is refused (its lease), never overwritten. Pushing over SSH: replace the URL by your remote's.\n"
        f"exec git -C '{mirror}' push {' '.join(leases)} '{origin}' 'refs/heads/*:refs/heads/*' 'refs/tags/*:refs/tags/*'\n"
    )


def rewrite(workdir: Path, values_from: str, origin: str, main_at: str | None) -> int:
    workdir = workdir.resolve()
    existing = next(p for p in (workdir, *workdir.parents) if p.exists())
    inside = subprocess.run(
        ["git", "-C", str(existing), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, check=False
    )
    if workdir.is_relative_to(ROOT) or inside.stdout.strip() == "true":
        sys.exit(f"refused: {workdir} is inside a checkout; use a folder of its own")
    workdir.mkdir(parents=True, exist_ok=True)
    if shutil.which("git-filter-repo") is None:
        sys.exit("git filter-repo is not installed (pip install git-filter-repo)")
    mirror = workdir / "mirror.git"
    if mirror.exists():
        sys.exit(f"refused: {mirror} exists; a rewrite starts from a fresh mirror")
    subprocess.run(["git", "clone", "--quiet", "--mirror", origin, str(mirror)], check=True)
    if main_at:
        # Dry run before this ticket's PR merges: stand `main` on a local commit that descends from it.
        _git(mirror, "fetch", "--quiet", str(ROOT), main_at)
        fetched = _git(mirror, "rev-parse", "FETCH_HEAD").decode().strip()
        _git(mirror, "merge-base", "--is-ancestor", "refs/heads/main", fetched)
        _git(mirror, "update-ref", "refs/heads/main", fetched)
        print(f"main simulated at {fetched[:9]} (a local descendant of origin's main), for the dry run only")

    replacements = workdir / "replacements.txt"
    count = _replacements(values_from, replacements)
    print(f"replacements: {count} values, written to {replacements} (outside every checkout)")

    before_tips = dict(
        line.split() for line in _git(mirror, "for-each-ref", "--format=%(refname) %(objectname)").decode().splitlines()
    )
    main_tree = _git(mirror, "rev-parse", "refs/heads/main^{tree}").decode().strip()
    print("\n== before")
    pulls_before = _report(*inventory(mirror))

    args = [
        "git",
        "-C",
        str(mirror),
        "filter-repo",
        "--force",
        "--replace-text",
        str(replacements),
        "--replace-message",
        str(replacements),
    ]
    args.append("--invert-paths")
    for path in REMOVED_PATHS:
        args += ["--path", path]
    try:
        subprocess.run(args, check=True, capture_output=True)
    finally:
        replacements.unlink()

    print("\n== after (refs/pull/* rewritten in this mirror only: GitHub keeps its own)")
    findings, reaching, messages = inventory(mirror)
    _report(findings, reaching, messages)
    after_tips = dict(
        line.split() for line in _git(mirror, "for-each-ref", "--format=%(refname) %(objectname)").decode().splitlines()
    )
    main_tree_after = _git(mirror, "rev-parse", "refs/heads/main^{tree}").decode().strip()
    moved = [r for r in before_tips if r.startswith("refs/heads/") and after_tips.get(r) != before_tips[r]]
    kept = [r for r in before_tips if r.startswith("refs/heads/") and after_tips.get(r) == before_tips[r]]
    print(
        f"main's tree: {main_tree[:12]} before, {main_tree_after[:12]} after — {'identical' if main_tree == main_tree_after else 'CHANGED'}"
    )
    print(f"branch tips rewritten: {len(moved)}; unchanged: {len(kept)}")
    for ref in moved:
        print(f"  {ref}\t{before_tips[ref][:9]} -> {after_tips.get(ref, 'gone')[:9]}")
    # Every commit SHA changes, not only those after the first finding: `git fast-export` drops the
    # signatures of GitHub's signed merge commits, so even the untouched ones are rewritten.
    commit_map = mirror / "filter-repo" / "commit-map"
    pairs = [line.split() for line in commit_map.read_text().splitlines()[1:]]
    renamed = sum(1 for old, new in pairs if old != new)
    print(f"commits with a new SHA: {renamed} of {len(pairs)}; old -> new in {commit_map} (SHAs only: keep it)")

    gone = [r for r in before_tips if r.startswith(("refs/heads/", "refs/tags/")) and r not in after_tips]
    for ref in gone:
        # filter-repo drops a branch it empties; a force-push would never delete it on GitHub.
        print(f"  EMPTIED by the rewrite, would keep its old history on GitHub: {ref}")
    clean = not findings and not messages and not gone and main_tree == main_tree_after
    print(
        "\nRESULT:",
        "clean — every finding gone from every ref, main's tree unchanged" if clean else "NOT CLEAN — do not push",
    )
    if clean and main_at:
        print("\nmain was simulated: no push script. The real run is made without --main-at, after the merge.")
    elif clean:
        push = workdir / "push.sh"
        push.write_text(push_script(mirror, origin, before_tips), encoding="utf-8")
        os.chmod(push, 0o755)
        print(f"\nAnto's force-push: {push} (one lease per branch and tag)")
    if clean:
        print(f"\nGitHub Support, after the push: drop the refs/pull/* and cached views of {len(pulls_before)} PRs:")
        print(f"  {_ranges(pulls_before)}")
    return 0 if clean else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory", help="scan every ref of a mirror clone")
    inv.add_argument("mirror", type=Path)
    rw = sub.add_parser("rewrite", help="dry run: fresh mirror, filter-repo, verify; never pushes")
    rw.add_argument("workdir", type=Path)
    rw.add_argument(
        "--values-from", required=True, help="REV:PATH of a guard test that names the values (on the ticket)"
    )
    rw.add_argument("--origin", default=ORIGIN)
    rw.add_argument("--main-at", help="dry run only: a local commit descending from origin's main to stand main on")
    args = parser.parse_args()
    if args.command == "inventory":
        findings, reaching, messages = inventory(args.mirror)
        _report(findings, reaching, messages)
        return 0
    return rewrite(args.workdir, args.values_from, args.origin, args.main_at)


if __name__ == "__main__":
    raise SystemExit(main())
