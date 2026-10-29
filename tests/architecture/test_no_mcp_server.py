"""KAN-490 (identAPI test agreement §3; KAN-484): provider-licensed vehicle
data is never exposed through an MCP server, a tool manifest or any other
agent-facing API.

The agreement prohibits implementing the provider's data in an MCP server
(Model Context Protocol). Nothing in the application does today; this keeps
an «expose the DMS to agents» change from doing it by accident. It fails when:

- a module under `app/` imports an MCP SDK, or declares an MCP server or tool
  (`FastMCP(…)`, `@mcp.tool`, a `tools/list` / `tools/call` handler);
- a file under `app/` or the frontend's sources is an MCP manifest
  (`.mcp.json`, `mcp.json`, …), or a frontend source imports an MCP SDK;
- `pyproject.toml` or a frontend `package.json` declares an MCP dependency
  that `ALLOWED_MCP_DEPENDENCIES` does not name, with its reason.

An entry in that allow-list is Anto's ruling by name, never a way around the
rule: KAN-227's prompt hook may use provider data only inside the tenant's own
request and response, never as a tool the model can call to fetch more
(`.claude/rules/vehicle.md`). The Claude Code tooling of this repository
(`.claude/`) is not the application and is not scanned.
"""

import ast
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
FRONTEND = ROOT / "frontend"
# Every workspace manifest, so a new package is scanned without an edit here.
PACKAGE_JSONS = tuple(sorted(p for p in FRONTEND.rglob("package.json") if "node_modules" not in p.parts))

# name -> why it is allowed (Anto's ruling, by name). Empty: none is.
ALLOWED_MCP_DEPENDENCIES: dict[str, str] = {}

_SERVER_DECLARATION = re.compile(
    r"\bFastMCP\s*\(|\bMcpServer\s*\(|@\s*\w*mcp\w*\.(?:tool|resource|prompt)\b|['\"]tools/(?:list|call)['\"]",
    re.IGNORECASE,
)
_TS_MCP_IMPORT = re.compile(
    r"""(?:from\s+|import\s*\(\s*|import\s+|require\s*\(\s*)['"](?:@modelcontextprotocol/[^'"]*|[^'"]*mcp[^'"]*)['"]""",
    re.IGNORECASE,
)
_SCRIPT_SUFFIXES = {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"}
_SKIPPED_DIRS = {"node_modules", "dist", "__pycache__"}


def is_mcp_name(name: str) -> bool:
    """A package or module name that is an MCP SDK or server: `mcp`, `fastmcp`,
    `mcp-server-…`, `fastapi-mcp`, `@modelcontextprotocol/sdk`, …"""

    lowered = name.lower()
    return "mcp" in lowered or "modelcontextprotocol" in lowered


def is_mcp_manifest(filename: str) -> bool:
    return bool(re.fullmatch(r"(?:.*[._-])?mcp(?:[._-].*)?\.(?:json|ya?ml|toml)", filename.lower()))


def mcp_imports(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names if is_mcp_name(alias.name.split(".")[0])]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0 and is_mcp_name(node.module.split(".")[0]):
            found.append(node.module)
    return found


def _requirement_name(requirement: str) -> str:
    return re.split(r"[\s\[<>=!~;@]", requirement.strip(), maxsplit=1)[0]


def declared_dependencies() -> dict[str, list[str]]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    python = [_requirement_name(r) for r in project.get("dependencies", [])]
    groups = [*project.get("optional-dependencies", {}).values(), *pyproject.get("dependency-groups", {}).values()]
    for group in groups:
        # a PEP 735 group may include another (`{include-group = …}`), which names no package
        python += [_requirement_name(r) for r in group if isinstance(r, str)]
    found = {"pyproject.toml": python}
    for path in PACKAGE_JSONS:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        names = []
        for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            names += list(manifest.get(key, {}))
        found[str(path.relative_to(ROOT))] = names
    return found


def _files(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and not _SKIPPED_DIRS.intersection(path.relative_to(root).parts):
            yield path


# --- the rule ---------------------------------------------------------------


def test_no_application_module_imports_an_mcp_sdk_or_declares_a_server():
    hits = []
    for path in APP.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        hits += [f"{path.relative_to(ROOT)}: import {name}" for name in mcp_imports(source)]
        hits += [f"{path.relative_to(ROOT)}: {m.group(0)}" for m in _SERVER_DECLARATION.finditer(source)]
    assert hits == [], "MCP in the application (identAPI agreement §3, KAN-490): " + "; ".join(hits)


def test_no_mcp_manifest_and_no_frontend_mcp_import():
    hits = []
    for root in (APP, FRONTEND):
        for path in _files(root):
            if is_mcp_manifest(path.name):
                hits.append(str(path.relative_to(ROOT)))
            elif path.suffix in _SCRIPT_SUFFIXES and _TS_MCP_IMPORT.search(path.read_text(encoding="utf-8")):
                hits.append(f"{path.relative_to(ROOT)}: imports an MCP SDK")
    assert hits == [], "MCP manifest or import (identAPI agreement §3, KAN-490): " + "; ".join(hits)


def test_no_mcp_dependency_without_an_allow_list_entry():
    hits = [
        f"{manifest}: {name}"
        for manifest, names in declared_dependencies().items()
        for name in names
        if is_mcp_name(name) and name not in ALLOWED_MCP_DEPENDENCIES
    ]
    assert hits == [], "MCP dependency without Anto's ruling (identAPI agreement §3, KAN-490): " + "; ".join(hits)


def test_every_allow_list_entry_names_its_reason():
    assert all(reason.strip() for reason in ALLOWED_MCP_DEPENDENCIES.values())


def test_every_frontend_workspace_manifest_is_read():
    assert {str(p.relative_to(ROOT)) for p in PACKAGE_JSONS} >= {
        "frontend/package.json", "frontend/apps/dms/package.json", "frontend/packages/ui-kit/package.json",
    }


# --- the guard catches what it is for ------------------------------------------


def test_the_guard_recognises_mcp_sdks_servers_and_manifests():
    assert mcp_imports("import mcp\nfrom mcp.server.fastmcp import FastMCP\nfrom fastmcp import FastMCP\n") == [
        "mcp", "mcp.server.fastmcp", "fastmcp",
    ]
    assert mcp_imports("from app.vehicle.public import get_configuration_for_host\nimport json\n") == []
    for declaration in ('server = FastMCP("dms")', "@mcp.tool()\ndef f(): ...", 'if method == "tools/call":'):
        assert _SERVER_DECLARATION.search(declaration), declaration
    assert not _SERVER_DECLARATION.search("def list_tools_for_the_workshop(): ...")
    for name in ("mcp", "fastmcp", "fastapi-mcp", "mcp-server-fetch", "@modelcontextprotocol/sdk"):
        assert is_mcp_name(name), name
    for name in ("fastapi", "@mantine/core", "pydantic", "sqlalchemy"):
        assert not is_mcp_name(name), name
    for filename in (".mcp.json", "mcp.json", "claude.mcp.json", "mcp-manifest.yaml"):
        assert is_mcp_manifest(filename), filename
    assert not is_mcp_manifest("package.json")
    assert _TS_MCP_IMPORT.search("import { Server } from '@modelcontextprotocol/sdk/server/index.js'")
    assert _TS_MCP_IMPORT.search('import { FastMCP } from "fastmcp"')
    assert _TS_MCP_IMPORT.search("import '@modelcontextprotocol/sdk'")
    assert not _TS_MCP_IMPORT.search("import { Button } from '@mantine/core'")
