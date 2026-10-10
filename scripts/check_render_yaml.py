"""Validate render.yaml against Render's published Blueprint schema (KAN-236).

    python scripts/check_render_yaml.py                   # validates ./render.yaml
    python scripts/check_render_yaml.py path/to/file.yaml # another Blueprint
    python scripts/check_render_yaml.py --refresh-schema  # re-download the vendored schema

Exit 0 when the file parses as YAML and validates; 1 with every problem listed,
one per line, otherwise.

What it catches: a file that is not YAML, unknown plan or region names, unknown
or misspelt keys, wrong value types, missing required fields. What it cannot
catch: anything Render decides against the live service's state — the downgrade
it refused in KAN-234 ("cannot downgrade database from 0.1c-256mb to Free") is a
valid document by this schema, and so is a background worker on `plan: free`,
which Render has no instance type for. Those still show up only in the Render
dashboard after the push.

The schema is vendored at scripts/dev/render.yaml.schema.json next to
render.yaml.schema.meta.json (source URL, fetch date, sha256), so the check runs
offline: CI runners and cloud sessions sit behind proxies, and a check that needs
the network to pass fails for the wrong reason. Render changes the schema when it
adds plans or fields; `--refresh-schema` re-downloads it and rewrites the sidecar,
and that diff is reviewed like any other.

Why this exists: scripts/dev/check chose lanes by path and nothing covered
render.yaml, so a change to it recorded a PASS without running anything, and CI
had no job for it either. The `deploy` lane and CI's mypy job both run this
script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import yaml
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_URL = "https://render.com/schema/render.yaml.json"
SCHEMA_PATH = ROOT / "scripts" / "dev" / "render.yaml.schema.json"
META_PATH = ROOT / "scripts" / "dev" / "render.yaml.schema.meta.json"


def validate(blueprint: Path, schema_path: Path = SCHEMA_PATH) -> list[str]:
    """Every problem with ``blueprint`` as one line each; an empty list means valid."""
    try:
        document = yaml.safe_load(blueprint.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"{blueprint}: not valid YAML: {exc}"]
    if not isinstance(document, dict):
        return [f"{blueprint}: expected a mapping at the top level, got {type(document).__name__}"]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    declared = _declared_root_keys(schema)
    lines: set[str] = set()
    for error in jsonschema.Draft202012Validator(schema).iter_errors(document):
        for path, message in _describe(error, declared):
            lines.add(f"{blueprint}: {path or '<root>'}: {message}")
    return sorted(lines)


def _declared_root_keys(schema: dict[str, object]) -> set[str]:
    """Top-level keys the schema knows (services, databases, envVarGroups, previews, ...),
    collected through its allOf branches and $ref targets."""
    keys: set[str] = set()

    def walk(node: object, depth: int) -> None:
        if not isinstance(node, dict) or depth > 4:
            return
        properties = node.get("properties")
        if isinstance(properties, dict):
            keys.update(properties)
        for keyword in ("allOf", "anyOf", "oneOf"):
            branches = node.get(keyword)
            if isinstance(branches, list):
                for branch in branches:
                    walk(branch, depth + 1)
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            target: object = schema
            for part in ref[2:].split("/"):
                target = target.get(part) if isinstance(target, dict) else None
            walk(target, depth + 1)

    walk(schema, 0)
    return keys


def _describe(error: ValidationError, declared: set[str]) -> Iterable[tuple[str, str]]:
    """One (path, message) per reported problem.

    The root schema wraps its resources in allOf + unevaluatedProperties: false, so any
    error under `services` or `databases` also produces a root-level "'services',
    'databases' were unexpected" that names nothing wrong and sorts first. That line
    is kept only for keys the schema does not declare at all (a misspelt `servicez`).

    A oneOf/anyOf failure (every service kind is one branch) is explained by the
    errors of its closest branch rather than by "<the whole mapping> is not valid
    under any of the given schemas"."""
    base = "/".join(str(part) for part in error.absolute_path)
    if not base and error.validator == "unevaluatedProperties":
        unknown = set(re.findall(r"'([^']+)'", error.message)) - declared
        if unknown:
            yield base, "unknown top-level key(s): " + ", ".join(sorted(unknown))
        return
    if not error.context:
        yield base, error.message
        return
    by_branch: dict[int, list[ValidationError]] = defaultdict(list)
    for sub in error.context:
        by_branch[int(sub.relative_schema_path[0])].append(sub)
    # A branch whose only complaint is the discriminator (`type: worker` is not `web`)
    # is simply the wrong kind of service; prefer branches that accept the kind.
    candidates = [subs for subs in by_branch.values() if not all(_is_discriminator_mismatch(s) for s in subs)]
    closest = min(candidates or list(by_branch.values()), key=len)
    label = error.instance.get("name") if isinstance(error.instance, dict) else None
    for sub in closest:
        path = (
            "/".join([base] + [str(part) for part in sub.relative_path])
            if base
            else "/".join(str(part) for part in sub.relative_path)
        )
        prefix = f"{label}: " if label else ""
        yield path, prefix + sub.message


def _is_discriminator_mismatch(sub: ValidationError) -> bool:
    return list(sub.relative_path) == ["type"] and sub.validator in ("enum", "const")


def refresh_schema(url: str = SCHEMA_URL, schema_path: Path = SCHEMA_PATH, meta_path: Path = META_PATH) -> None:
    """Re-download the published schema and record where and when it came from."""
    with urllib.request.urlopen(url, timeout=30) as response:
        raw = response.read()
    json.loads(raw)  # refuse to vendor anything that is not JSON
    schema_path.write_bytes(raw)
    meta = {
        "source": url,
        "fetched": datetime.now(UTC).date().isoformat(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "refresh": "python scripts/check_render_yaml.py --refresh-schema",
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Render Blueprint against Render's published schema.")
    parser.add_argument("blueprint", nargs="?", default=str(ROOT / "render.yaml"))
    parser.add_argument(
        "--refresh-schema",
        action="store_true",
        help=f"re-download {SCHEMA_URL} into {SCHEMA_PATH.relative_to(ROOT)} before validating",
    )
    args = parser.parse_args(argv)
    if args.refresh_schema:
        refresh_schema()
        print(f"refreshed {SCHEMA_PATH.relative_to(ROOT)} from {SCHEMA_URL}")
    problems = validate(Path(args.blueprint))
    for line in problems:
        print(line)
    if problems:
        return 1
    print(f"{args.blueprint}: valid against {SCHEMA_URL} (vendored copy, see {META_PATH.relative_to(ROOT)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
