"""Proves a move of the UI-string bundles changed no text (KAN-616 exit criterion 4).

Flattens every language's bundle to `dotted.key -> text`, once as committed at a git ref and
once from the working tree, and prints every key that was added, removed or whose text differs.
Either side may use either layout: the one-file-per-language `locales/<language>.json` of
before KAN-616, or `locales/<language>/<namespace>[.<key>].json`
(scripts/split_locale_bundles.py). Exit 0 with zero differences, 1 otherwise.

    python3 scripts/compare_locale_bundles.py               # against origin/main
    python3 scripts/compare_locale_bundles.py --ref 7e4819f
"""

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCALES = "frontend/apps/dms/src/i18n/locales"
LANGUAGES = ("de", "fr", "it", "en")


def flatten(tree: object, prefix: str = "") -> dict[str, str]:
    if not isinstance(tree, dict):
        return {prefix: json.dumps(tree, ensure_ascii=False)}
    flat: dict[str, str] = {}
    for key, value in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        flat.update(flatten(value, path) if isinstance(value, dict) else {path: value})
    return flat


def assemble(language: str, names: list[str], read: Callable[[str], str]) -> dict:
    """One language's bundle from whichever layout `names` (paths under the locales dir) shows."""
    if f"{language}.json" in names:
        return json.loads(read(f"{language}.json"))
    bundle: dict = {}
    for name in sorted(n for n in names if n.startswith(f"{language}/") and n.endswith(".json")):
        parts = name.removeprefix(f"{language}/").removesuffix(".json").split(".")
        content = json.loads(read(name))
        if len(parts) == 1 and isinstance(content, dict):
            bundle.setdefault(parts[0], {}).update(content)
        elif len(parts) == 1:
            bundle[parts[0]] = content
        else:
            bundle.setdefault(parts[0], {})[parts[1]] = content
    return bundle


def at_ref(ref: str) -> dict[str, dict]:
    def git(*args: str) -> str:
        return subprocess.run(("git", *args), cwd=ROOT, check=True, capture_output=True, text=True).stdout

    names = [n.removeprefix(LOCALES + "/") for n in git("ls-tree", "-r", "--name-only", ref, "--", LOCALES).split()]
    return {lang: assemble(lang, names, lambda name: git("show", f"{ref}:{LOCALES}/{name}")) for lang in LANGUAGES}


def working_tree() -> dict[str, dict]:
    base = ROOT / LOCALES
    names = [str(p.relative_to(base)) for p in base.rglob("*.json")]
    return {lang: assemble(lang, names, lambda name: (base / name).read_text(encoding="utf-8")) for lang in LANGUAGES}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ref", default="origin/main", help="the git ref to compare against (default origin/main)")
    args = parser.parse_args(argv)
    before, after = at_ref(args.ref), working_tree()
    total = 0
    for lang in LANGUAGES:
        old, new = flatten(before[lang]), flatten(after[lang])
        added = sorted(new.keys() - old.keys())
        removed = sorted(old.keys() - new.keys())
        changed = sorted(k for k in old.keys() & new.keys() if old[k] != new[k])
        total += len(added) + len(removed) + len(changed)
        print(f"{lang}: {len(old)} keys at {args.ref}, {len(new)} in the working tree - "
              f"{len(added)} added, {len(removed)} removed, {len(changed)} changed text")
        for label, keys in (("added", added), ("removed", removed), ("changed", changed)):
            for key in keys[:20]:
                print(f"  {label}: {key}")
    print(f"{total} differences")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
