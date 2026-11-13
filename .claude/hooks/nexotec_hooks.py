#!/usr/bin/env python3
"""Nexotec's Claude Code hooks and the `scripts/dev/gate` command line.

One file on purpose: every gate reads and writes the same state, and one file
is easier to review than eight. Standard library only, Python 3.8+, because
hooks run with whatever `python3` the machine has, not the project's .venv.

How the gates stay exact
------------------------
"Green", "reviewed" and "screenshotted" are recorded against a git TREE hash:
the tree the working copy would produce if everything in it were committed.
A push is only allowed when the local check passed for exactly the tree being
pushed, and the hand-over only passes when the review and the screenshot are
for that same tree. Any later edit, rebase or merge changes the tree and so
invalidates the evidence. Nothing here trusts a timestamp alone.

State lives in `<git dir>/nexotec-gates/state.json` - per worktree, never
committed, removed with the worktree. Writers hold `state.lock` (flock), so
hooks that fire at the same moment never lose each other's events.

Shell commands are parsed, not pattern-matched: quotes, here-documents,
`$(...)`, subshells, pipes, wrappers such as `timeout`/`nohup`/`env`, and
`sh -c` scripts are seen through, so a push or a write cannot hide in an
unusual shape - and text inside a commit message is never taken for a command.

Subcommands (hooks)            | CLI (`scripts/dev/gate <cmd>`)
-------------------------------+---------------------------------------------
session-start  (SessionStart)  | start KAN-n <notion url|id>   active ticket
session-sync   (async)         | status                        what gates want
pre-bash       (PreToolUse)    | waive <gate> "<reason>"       (asks Anto)
pre-edit       (PreToolUse)    | no-visual "<why>"             no screen exists
pre-read       (PreToolUse)    |
pre-notion     (PreToolUse)    | evidence screenshot <file>    register a file
pre-render     (PreToolUse)    | review-scope <sha>            re-review allowed?
post-tool      (PostToolUse)   | close [--abandon]             after the merge
subagent-stop  (SubagentStop)  | fingerprint / stamp-checks    used by check
stop           (Stop)          | selftest                      used by check

Failure policy: an internal error in a hook never blocks work (exit 1, which
Claude Code treats as a non-blocking error, with the traceback on stderr). A
gate that cannot decide says so in its reason.

The gate state is written only by these hooks and `scripts/dev/gate`: writes
to it, direct `stamp-checks` calls and direct `cli waive` calls are denied.
"""

import contextlib
import datetime
import glob
import io
import json
import os
import re
import shutil
import socket
import subprocess
import urllib.parse
import sys
import tempfile
import traceback

try:
    import fcntl
except ImportError:  # pragma: no cover - not on macOS or Linux
    fcntl = None

# --------------------------------------------------------------------------
# Constants - the few facts about the project the gates need
# --------------------------------------------------------------------------

GENERATED_FILES = {
    "frontend/apps/dms/src/api/schema.d.ts": (
        "schema.d.ts is generated from the backend's OpenAPI schema (KAN-35). "
        "Change the backend model or schema instead, then run "
        "`make generate-frontend-types`."
    ),
}
# KAN-618: generated files far larger than a context window. The `Read` hook (`pre-read`) and the
# pre-bash hook refuse printing them whole - a lookup or `grep -n` instead. Not a `Read(...)` deny
# rule in settings.json: Claude Code applies one to `grep` on the same path too.
WHOLE_READ_GUARDED = {
    "frontend/apps/dms/src/api/schema.d.ts": "~800 KB of generated API types",
    "frontend/apps/dms/openapi.json": "the generated OpenAPI document",
    "frontend/package-lock.json": "~120 KB of lockfile",
}
WHOLE_READ_MAX_LINES = 200
GENERATED_SUFFIXES = {
    "nexotec-prototype.html": (
        "nexotec-prototype.html is a built file, and the prototype is maintained from the spec side "
        "(.claude/rules/ui.md)."
    ),
}
MIGRATIONS_DIR = "alembic/versions/"
MAIN_BRANCHES = ("main", "master")
# CLAUDE.md "Preserve verbatim" - writes ask Anto, whichever tool makes them.
PRESERVED_FILES = (
    "app/core/tenancy.py", "app/core/auth.py", "app/core/uuid7.py", "app/core/base.py",
    "app/core/concurrency.py", "app/core/idempotency.py", "app/core/pagination.py",
    "app/core/errors.py", "app/core/schemas.py", "app/core/audit.py", "app/core/consumer.py",
    "app/core/processed_event_model.py", "app/core/types.py", "app/core/config.py",
)
PRESERVED_PATTERN = re.compile(r"^app/core/outbox[^/]*\.py$")
# The gates themselves - a change to them asks Anto.
SETUP_PREFIXES = (".claude/hooks/", ".claude/agents/", "scripts/dev/")
SETUP_FILES = (".claude/settings.json",)
# "Nexotec Kanban Board": its database id and its data source id.
KANBAN_IDS = {"3cf3e79334dd80f69bb8c2e6a19481ef", "3cf3e79334dd805c9017000b5a24915f"}
# Writes to the active ticket's page count as its hand-over evidence (PostToolUse).
NOTION_TICKET_WRITES = ("notion-update-page", "notion-create-comment")
NOTION_READ_PREFIXES = (
    "notion-fetch", "notion-search", "notion-query", "notion-get", "notion-list",
    "notion-ai-search", "notion-read", "notion-download", "notion-check",
    "notion-show", "notion-wait",
)
# Render's connector (KAN-686; Anto, 2026-11-13: sessions may read staging, read-only SQL included).
# Every tool the connector offers, recorded from a cloud session on 2026-11-13. settings.json allows
# the reads and denies every other tool by name under each server name; the selftest fails when a
# recorded tool is in neither list, and the pre-render hook denies any Render tool not a read, under
# whatever name the server appears (a third name would otherwise reach the default permission flow).
RENDER_READ_TOOLS = (
    "list_workspaces", "list_services", "get_service", "list_deploys", "get_deploy", "list_events", "list_logs",
    "list_log_label_values", "get_metrics", "list_postgres_instances", "get_postgres", "query_render_postgres",
)
RENDER_TOOLS = RENDER_READ_TOOLS + (
    "approve_blueprint", "cancel_flow_run", "create_blueprint", "create_cron_job", "create_key_value",
    "create_postgres", "create_static_site", "create_web_service", "create_workflow", "create_workflow_version",
    "get_blueprint", "get_flow", "get_flow_run", "get_flow_run_results", "get_flow_run_step", "get_key_value",
    "get_selected_workspace", "get_workflow", "get_workflow_run", "get_workflow_run_results", "get_workflow_task",
    "get_workflow_version", "list_flow_run_steps", "list_flow_runs", "list_flows", "list_key_value",
    "list_workflow_runs", "list_workflow_tasks", "list_workflow_versions", "list_workflows", "plan_blueprint",
    "select_workspace", "start_flow_run", "trigger_deploy", "update_blueprint", "update_environment_variables",
)
# `mcp__Render` in a cloud session (seen 2026-11-13); `mcp__claude_ai_Render` on the Mac by the Notion
# connector's pattern - not yet read off a Mac session (KAN-686).
RENDER_SERVERS = ("mcp__Render", "mcp__claude_ai_Render")
# Screenshots count as evidence only from the desktop preview / built-in browser.
PREVIEW_TOOLS = ("claude_preview", "claude_browser")
COMPLETION_CLAIM = re.compile(
    r"\b(done|fixed|finished|completed?|shipped|merged|ready for review|"
    r"fertig|erledigt|abgeschlossen)\b",
    re.IGNORECASE,
)
REVIEW_VERDICT = re.compile(r"VERDICT:\s*(PASS|FINDINGS)", re.IGNORECASE)
REVIEWED_COMMIT = re.compile(r"REVIEWED:\s*`?([0-9a-fA-F]{7,40})\b")
# The commit status that carries a session's check result to the PR (KAN-625). GitHub cuts a
# status description at 140 characters.
CHECK_STATUS_CONTEXT = "nexotec/check"
CHECK_STATUS_MAX = 140
# A re-review (KAN-620) names the commit the previous review ruled on. It is narrow, so it is
# allowed only for a small range that touches nothing a full review must see. The marker is a
# line of its own, as reviewer.md writes it: prose that says "re-review of <sha>" is not one.
REREVIEW_OF = re.compile(r"^\W{0,4}RE-REVIEW OF:\W{0,4}([0-9a-fA-F]{7,40})\b", re.MULTILINE)
REREVIEW_MAX_LINES = 150
# After a push, a stop that needs Anto's decision says so with this marker.
DECISION_NEEDED = re.compile(r"^\W{0,6}decision needed\W{0,3}:", re.IGNORECASE | re.MULTILINE)
PG_HOST, PG_PORT = "localhost", 5432

# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def run(cmd, cwd=None, env=None, timeout=30):
    """Run a command; return (returncode, stdout). Never raises for a non-zero exit."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def git(cwd, *args, **kw):
    return run(["git"] + list(args), cwd=cwd, **kw)


def toplevel(cwd):
    if not cwd or not os.path.isdir(cwd):
        return None
    rc, out = git(cwd, "rev-parse", "--show-toplevel")
    return out if rc == 0 and out else None


def git_dir(root):
    rc, out = git(root, "rev-parse", "--absolute-git-dir")
    return out if rc == 0 else None


def common_dir(root):
    rc, out = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return out if rc == 0 else None


def current_branch(root):
    rc, out = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    return out if rc == 0 else None


def head_tree(root):
    rc, out = git(root, "rev-parse", "HEAD^{tree}")
    return out if rc == 0 else None


def head_commit(root):
    rc, out = git(root, "rev-parse", "HEAD")
    return out if rc == 0 else None


def is_worktree(root):
    gd, cd = git_dir(root), common_dir(root)
    return bool(gd and cd and os.path.normpath(gd) != os.path.normpath(cd))


def main_checkout(root):
    cd = common_dir(root)
    return os.path.dirname(cd) if cd else None


def fingerprint(root):
    """Tree hash of the working copy as if everything (tracked + untracked,
    minus ignored) were committed. Uses a throwaway index, so the real index
    and the working copy are never touched."""
    gd = git_dir(root)
    if not gd:
        return None
    fd, tmp_index = tempfile.mkstemp(prefix="nexotec-fp-", suffix=".index")
    os.close(fd)
    try:
        real_index = os.path.join(gd, "index")
        if os.path.exists(real_index):
            shutil.copyfile(real_index, tmp_index)  # keeps stat caches: fast
        else:
            os.remove(tmp_index)
        env = dict(os.environ, GIT_INDEX_FILE=tmp_index)
        rc, _ = run(["git", "add", "-A"], cwd=root, env=env, timeout=120)
        if rc != 0:
            return None
        rc, tree = run(["git", "write-tree"], cwd=root, env=env)
        return tree if rc == 0 else None
    finally:
        for path in (tmp_index, tmp_index + ".lock"):
            try:
                os.remove(path)
            except OSError:
                pass


def status_lines(root):
    """`git status --porcelain` lines, leading status columns intact."""
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=root, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, universal_newlines=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line for line in out.splitlines() if line.strip()]


def slug(text):
    s = re.sub(r"[^a-z0-9_]+", "_", (text or "").lower()).strip("_")
    return s or "x"


def worktree_slug(root):
    return "root" if not is_worktree(root) else slug(os.path.basename(root))[:40]


def test_db_name(root):
    return "dms_test_" + worktree_slug(root)


def test_db_url(root):
    return "postgresql+psycopg://dms:dms@{}:{}/{}".format(PG_HOST, PG_PORT, test_db_name(root))


def postgres_reachable(timeout=0.3):
    try:
        with socket.create_connection((PG_HOST, PG_PORT), timeout=timeout):
            return True
    except OSError:
        return False


UUID_RE = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{8})-([0-9a-fA-F]{4})-([0-9a-fA-F]{4})-"
                     r"([0-9a-fA-F]{4})-([0-9a-fA-F]{12})(?![0-9a-fA-F])")
HEX32_RE = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{32})(?![0-9a-fA-F])")


def notion_id(value):
    """Normalise a Notion page/database id or URL to 32 lowercase hex chars.
    The first id wins: in a page URL the page id comes before any ?v= view id."""
    if not isinstance(value, str):
        return None
    m = UUID_RE.search(value)
    if m:
        return "".join(m.groups()).lower()
    m = HEX32_RE.search(value)
    return m.group(1).lower() if m else None


def all_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            for s in all_strings(v):
                yield s
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            for s in all_strings(v):
                yield s


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


def state_path(root):
    gd = git_dir(root)
    if not gd:
        return None
    d = os.path.join(gd, "nexotec-gates")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "state.json")


@contextlib.contextmanager
def state_lock(root):
    """Serialise read-modify-write of the state between concurrent hooks."""
    path = state_path(root)
    if not path or fcntl is None:
        yield
        return
    fh = open(os.path.join(os.path.dirname(path), "state.lock"), "a")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fh.close()  # closing the file releases the lock


def load_state(root):
    path = state_path(root)
    if path and os.path.exists(path):
        try:
            with open(path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    return {}


def save_state(root, state):
    path = state_path(root)
    if not path:
        return
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".state-")
    with os.fdopen(fd, "w") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def update_state(root, change):
    """Apply `change(state)` under the lock and save; returns what `change` returns."""
    with state_lock(root):
        state = load_state(root)
        result = change(state)
        events_list = state.get("events")
        if isinstance(events_list, list):
            del events_list[:-300]  # keep the file small
        save_state(root, state)
        return result


def append_event(root, kind, **fields):
    fields.update({"kind": kind, "at": now()})
    update_state(root, lambda state: state.setdefault("events", []).append(fields))


def events(state, kind):
    return [e for e in state.get("events", []) if e.get("kind") == kind]


def latest(items, **match):
    for item in reversed(items):
        if all(item.get(k) == v for k, v in match.items()):
            return item
    return None


def handover_open(state):
    """The latest `handover-open` event that no `handover-complete` followed, or None."""
    for e in reversed(state.get("events", [])):
        if e.get("kind") == "handover-complete":
            return None
        if e.get("kind") == "handover-open":
            return e
    return None


# --------------------------------------------------------------------------
# Hook I/O
# --------------------------------------------------------------------------


def read_input():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def emit(obj):
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def pre_decision(decision, reason):
    emit({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }})


def root_from(data):
    return toplevel(data.get("cwd") or os.getcwd())


# --------------------------------------------------------------------------
# Reading shell commands
# --------------------------------------------------------------------------

HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
SEPARATOR_CHARS = "\n;&|()"
WRITE_REDIRECTS = (">", ">>", ">|", "&>", "&>>", "<>")
SHELLS = ("bash", "sh", "zsh", "dash")
RESERVED_WORDS = ("!", "{", "}", "then", "do", "else", "elif", "if", "while", "until", "fi", "done", "esac",
                  "exec", "command", "builtin", "nohup", "noglob", "sudo")


def strip_heredocs(command):
    """Drop here-document bodies: their lines are data (commit messages), not commands."""
    out, pending = [], []
    for line in (command or "").split("\n"):
        if pending:
            strip_tabs, delim = pending[0]
            if (line.lstrip("\t") if strip_tabs else line) == delim:
                pending.pop(0)
            continue
        out.append(line)
        for m in HEREDOC_RE.finditer(line):
            pending.append((m.group(1) == "-", m.group(3)))
    return "\n".join(out)


def matching_paren(text, open_idx):
    """Index of the `)` that closes the `(` at `open_idx` (quotes respected)."""
    depth, i, n = 0, open_idx, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = text.find("'", i + 1)
            i = n if j == -1 else j + 1
            continue
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\":
                    i += 2
                    continue
                if text.startswith("$(", i):
                    i = matching_paren(text, i + 1) + 1
                    continue
                i += 1
            i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def split_commands(text):
    """Split shell text into simple commands: a list of (words, redirects), where
    redirects is a list of (operator, target). Operators outside quotes separate
    commands; `$(...)` and backquotes become commands of their own and leave a
    placeholder in the word that contained them."""
    commands = []
    state = {"words": [], "redirects": [], "word": None, "redirect": None}

    def add(chars):
        state["word"] = (state["word"] or "") + chars

    def finish_word():
        if state["word"] is not None:
            if state["redirect"]:
                if state["redirect"] != "heredoc":
                    state["redirects"].append((state["redirect"], state["word"]))
                state["redirect"] = None
            else:
                state["words"].append(state["word"])
            state["word"] = None

    def finish_command():
        finish_word()
        if state["words"] or state["redirects"]:
            commands.append((state["words"], state["redirects"]))
        state["words"], state["redirects"], state["redirect"] = [], [], None

    def substitution(start, inner_start, end):
        commands.extend(split_commands(text[inner_start:end]))
        return end + 1

    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            if text[i + 1] != "\n":
                add(text[i + 1])
            i += 2
            continue
        if c == "'":
            j = text.find("'", i + 1)
            j = n if j == -1 else j
            add(text[i + 1:j])
            i = j + 1
            continue
        if c == '"':
            i += 1
            buf = ""
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n and text[i + 1] in '"\\$`\n':
                    buf += text[i + 1]
                    i += 2
                    continue
                if text.startswith("$(", i) and not text.startswith("$((", i):
                    i = substitution(i, i + 2, matching_paren(text, i + 1))
                    buf += "$(...)"
                    continue
                if text[i] == "`":
                    j = text.find("`", i + 1)
                    j = n if j == -1 else j
                    i = substitution(i, i + 1, j)
                    buf += "`...`"
                    continue
                buf += text[i]
                i += 1
            add(buf)
            i += 1
            continue
        if text.startswith("$(", i) and not text.startswith("$((", i):
            i = substitution(i, i + 2, matching_paren(text, i + 1))
            add("$(...)")
            continue
        if c == "`":
            j = text.find("`", i + 1)
            j = n if j == -1 else j
            i = substitution(i, i + 1, j)
            add("`...`")
            continue
        if c in " \t":
            finish_word()
            i += 1
            continue
        if c == "#" and state["word"] is None:
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue
        if c in "<>" or (c == "&" and text[i + 1:i + 2] == ">"):
            if state["word"] is not None and state["word"].isdigit():
                state["word"] = None  # a file-descriptor prefix such as 2>
            else:
                finish_word()
            j = i
            while j < n and text[j] in "<>&|":
                j += 1
            op = text[i:j]
            i = j
            while i < n and text[i] in " \t":
                i += 1
            if op in (">&", "<&") and i < n and (text[i].isdigit() or text[i] == "-"):
                while i < n and (text[i].isdigit() or text[i] == "-"):
                    i += 1
                continue
            if op.startswith("<<") and op != "<<<":
                state["redirect"] = "heredoc"
            else:
                state["redirect"] = op
            continue
        if c in SEPARATOR_CHARS:
            finish_command()
            i += 1
            continue
        add(c)
        i += 1
    finish_command()
    return commands


def unwrap(words):
    """Drop what does not change which program runs: variable assignments,
    reserved words and wrappers (env, time, nice, timeout, nohup, stdbuf, xargs)."""
    w = list(words)
    while w:
        head, base = w[0], os.path.basename(w[0])
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", head):
            w = w[1:]
        elif base in RESERVED_WORDS:
            w = w[1:]
        elif base == "time":
            w = w[1:]
            while w and w[0] == "-p":
                w = w[1:]
        elif base == "env":
            w = w[1:]
            while w and w[0].startswith("-"):
                opt, w = w[0], w[1:]
                if opt in ("-u", "--unset", "-C", "--chdir") and w:
                    w = w[1:]
        elif base == "nice":
            w = w[1:]
            if w and w[0] in ("-n", "--adjustment") and len(w) > 1:
                w = w[2:]
            elif w and re.match(r"^(-n?-?\d+|--adjustment=.*)$", w[0]):
                w = w[1:]
        elif base == "timeout":
            w = w[1:]
            while w and w[0].startswith("-"):
                opt, w = w[0], w[1:]
                if opt in ("-s", "--signal", "-k", "--kill-after") and w:
                    w = w[1:]
            w = w[1:]  # the duration
        elif base == "stdbuf":
            w = w[1:]
            while w and w[0].startswith("-"):
                opt, w = w[0], w[1:]
                if opt in ("-i", "-o", "-e") and w:
                    w = w[1:]
        elif base == "xargs":
            w = w[1:]
            while w and w[0].startswith("-"):
                opt, w = w[0], w[1:]
                if opt in ("-I", "-L", "-n", "-P", "-d", "-E", "-s", "-a") and w:
                    w = w[1:]
        else:
            break
    return w


def shell_commands(command, depth=0):
    """Every simple command in `command` as (words, redirects): wrappers removed,
    here-document bodies dropped, `sh -c` / `eval` scripts expanded."""
    out = []
    for words, redirects in split_commands(strip_heredocs(command)):
        w = unwrap(words)
        if depth < 3 and w and os.path.basename(w[0]) in SHELLS and "-c" in w[1:]:
            idx = w.index("-c", 1)
            if idx + 1 < len(w):
                out.extend(shell_commands(w[idx + 1], depth + 1))
            continue
        if depth < 3 and w and w[0] == "eval":
            out.extend(shell_commands(" ".join(w[1:]), depth + 1))
            continue
        out.append((w, redirects))
    return out


def located_commands(command, cwd):
    """Yield (words, redirects, directory it runs in), following `cd` and `git -C`."""
    for words, redirects in shell_commands(command):
        if words and words[0] in ("cd", "pushd"):
            if len(words) > 1:
                cwd = os.path.normpath(os.path.join(cwd, os.path.expanduser(words[1])))
            continue
        where = cwd
        if words and os.path.basename(words[0]) == "git":
            i = 1
            while i < len(words) and words[i].startswith("-"):
                if words[i] == "-C" and i + 1 < len(words):
                    where = os.path.normpath(os.path.join(where, os.path.expanduser(words[i + 1])))
                i += 2 if words[i] in ("-C", "-c", "--git-dir", "--work-tree") else 1
        yield words, redirects, where


def git_verb(tokens):
    """For a git command return (verb, args) skipping global options like -C."""
    if not tokens or os.path.basename(tokens[0]) != "git":
        return None, []
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):
        i += 2 if tokens[i] in ("-C", "-c", "--git-dir", "--work-tree") else 1
    if i >= len(tokens):
        return None, []
    return tokens[i], tokens[i + 1:]


def option_letters(args, takes_value="", takes_attached=""):
    """Short-option letters in `args`, splitting clusters like `-uf`. A letter in
    `takes_value` consumes the rest of its cluster or, at the end of it, the next
    token; a letter in `takes_attached` consumes only the rest of its cluster."""
    letters, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--":
            break
        if len(a) < 2 or a[0] != "-" or a[1] == "-":
            continue
        body = a[1:]
        for i, ch in enumerate(body):
            letters.append(ch)
            if ch in takes_value:
                skip = i == len(body) - 1
                break
            if ch in takes_attached:
                break
    return letters


def operands(args):
    return [a for a in args if a and not a.startswith("-")]


def gate_cli_command(tokens):
    """The subcommand of a direct `scripts/dev/gate ...` or `nexotec_hooks.py cli ...`
    call, 'status' when none is given, None when `tokens` is not such a call."""
    for i, tok in enumerate(tokens[:3]):
        if tok.endswith("scripts/dev/gate"):
            return tokens[i + 1] if i + 1 < len(tokens) else "status"
        if tok.endswith("nexotec_hooks.py"):
            rest = tokens[i + 1:]
            if rest[:1] == ["cli"]:
                return rest[1] if len(rest) > 1 else "status"
            return None
    return None


# --------------------------------------------------------------------------
# git push
# --------------------------------------------------------------------------

PUSH_VALUE_OPTIONS = ("--push-option", "--repo", "--receive-pack", "--exec")
# git commands that move HEAD or change the tree: a push after one of them in the
# same command line would push something the gate never saw.
TREE_CHANGING_GIT = ("commit", "merge", "pull", "rebase", "cherry-pick", "revert", "am", "switch",
                     "checkout", "reset", "stash", "restore", "apply", "rm", "mv", "add")


def push_positionals(args):
    """The remote and refspecs of a `git push` argument list, options removed."""
    positional, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a in PUSH_VALUE_OPTIONS:
            skip = True
            continue
        if a.startswith("-"):
            if a[1:2] != "-" and "o" in a[1:]:
                skip = a[1:].index("o") == len(a) - 2  # `-o <option>`: skip its value
            continue
        positional.append(a)
    return positional


def push_refspecs(args):
    return push_positionals(args)[1:]


def push_pairs(args, branch):
    """(source, remote branch) for each ref a `git push` updates; HEAD stays HEAD."""
    refspecs = push_refspecs(args)
    if not refspecs:
        return [("HEAD", branch or "HEAD")]
    pairs = []
    for spec in refspecs:
        spec = spec[1:] if spec.startswith("+") else spec
        src, colon, dst = spec.partition(":")
        if not colon:
            dst = src
        if src in ("@", branch):
            src = "HEAD"
        if dst in ("HEAD", "@"):
            dst = branch or "HEAD"
        pairs.append((src, re.sub(r"^refs/heads/", "", dst)))
    return pairs


def push_destinations(args, branch):
    """Remote branch names a `git push` would update; HEAD resolves to `branch`."""
    return [dst for _src, dst in push_pairs(args, branch)]


def push_mode(args):
    """'dry-run', 'delete' or 'push' for a `git push` argument list."""
    letters = option_letters(args, takes_value="o")
    if "--dry-run" in args or "n" in letters:
        return "dry-run"
    refspecs = push_refspecs(args)
    if "--delete" in args or "d" in letters or (refspecs and all(s.startswith(":") for s in refspecs)):
        return "delete"
    return "push"


def fetch_main(root):
    """`git fetch origin main` for the push gate (KAN-683). (fetched, note): fetched is None when the
    checkout has no `origin` remote (nothing to judge by, nothing to say); a failed fetch is a note,
    never a refusal - the gate then judges by the last known origin/main."""
    if git(root, "remote", "get-url", "origin")[0] != 0:
        return None, None
    if git(root, "fetch", "--quiet", "origin", "main", timeout=30)[0] == 0:
        return True, None
    return False, ("The push gate could not fetch origin/main, so it judged by the last known origin/main "
                   "(KAN-683). If main has moved since, the merge gate will say so: run `git fetch origin main` "
                   "once the network is back.")


def behind_main_problem(root, src):
    """KAN-683: why the commit `src` would be pushed without current main in it, or None. Landing is one
    at a time: a head that does not contain origin/main was never checked together with it."""
    rc, main = git(root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main^{commit}")
    if rc != 0:
        return None
    rc, commit = git(root, "rev-parse", "--verify", "--quiet", src + "^{commit}")
    if rc != 0 or git(root, "merge-base", "--is-ancestor", main, commit)[0] == 0:
        return None
    rc, count = git(root, "rev-list", "--count", "{}..{}".format(commit, main))
    return ("`{}` does not contain origin/main ({}): {} commit(s) of main are not in it, so no check ever ran on "
            "this change together with them (KAN-683: landing is one at a time). Merge main in - never a rebase, "
            "the gate refuses a force-push - and check the result:\n"
            "    git merge --no-edit origin/main\n"
            "    scripts/dev/check\n"
            "then push again. Conflicts in generated files are regenerated, not merged "
            "(`make generate-frontend-types`).".format(src, main[:10], count if rc == 0 else "some"))


def push_problems(root, args, notes=None, fetch=True):
    """Why a `git push` with `args` is refused: [text]. `notes` (a list) collects what the gate says
    without refusing - a failed fetch of origin/main (KAN-683). `fetch=False` (`gate status`, which
    stays read-only) judges by the last fetched origin/main."""
    problems = []
    branch = current_branch(root)
    force = [a for a in args if a.startswith("--force") or (a.startswith("+") and len(a) > 1)]
    if "f" in option_letters(args, takes_value="o"):
        force.append("-f")
    if force:
        problems.append("Force pushes are never allowed (found `{}`). If the branch needs main's "
                        "changes, merge origin/main into it instead of rebasing.".format(force[0]))
    if "--no-verify" in args:
        problems.append("`--no-verify` is never allowed.")
    if any(a in ("--all", "--mirror", "--branches") for a in args):
        problems.append("Push one branch at a time - `--all` / `--mirror` would push `main` too.")
    if any(d in MAIN_BRANCHES for d in push_destinations(args, branch)):
        problems.append("Pushing to `main` is never allowed - push a branch and open a PR.")
    if problems or push_mode(args) != "push":
        return problems
    checks = load_state(root).get("checks") or {}
    last = ("{} for tree {}".format(checks.get("result"), (checks.get("tree") or "?")[:12])
            if checks.get("at") else "none")
    _fetched, note = fetch_main(root) if fetch else (None, None)
    if note and notes is not None:
        notes.append(note)
    for src, _dst in push_pairs(args, branch):
        behind = behind_main_problem(root, src)
        if behind:
            problems.append(behind)
        if src == "HEAD":
            tree = head_tree(root)
            fp = fingerprint(root)
            if fp and tree and fp != tree:
                problems.append("The working copy differs from HEAD, so HEAD is not what was checked. "
                                "Commit (or remove) these first:\n" +
                                "\n".join("    " + l for l in status_lines(root)[:15]))
        else:
            rc, tree = git(root, "rev-parse", "--verify", "--quiet", src + "^{tree}")
            if rc != 0:
                problems.append("Cannot resolve `{}` to a commit.".format(src))
                continue
        if not tree or checks.get("tree") != tree or checks.get("result") != "pass":
            problems.append(
                "No passing local check for the tree being pushed ({} -> {}). Last recorded check: {}. "
                "Run `scripts/dev/check` - it records a pass for the tree it verified - then push again."
                .format(src, (tree or "?")[:12], last))
    return problems


def push_landed(root, args):
    """True when every ref of a `git push` now matches its remote-tracking ref."""
    positional = push_positionals(args)
    remote = positional[0] if positional else "origin"
    branch = current_branch(root)
    pairs = [(s, d) for s, d in push_pairs(args, branch) if s]
    for src, dst in pairs:
        rc1, local = git(root, "rev-parse", "--verify", "--quiet", src + "^{commit}")
        rc2, tracked = git(root, "rev-parse", "--verify", "--quiet", "refs/remotes/{}/{}".format(remote, dst))
        if rc1 != 0 or rc2 != 0 or local != tracked:
            return False
    return bool(pairs)


# --------------------------------------------------------------------------
# Writes through the shell
# --------------------------------------------------------------------------


def write_targets(words, redirects, where):
    """Paths a simple command may write, move or delete, resolved against `where`."""
    targets = [t for op, t in redirects if op in WRITE_REDIRECTS]
    if words:
        base, args = os.path.basename(words[0]), words[1:]
        ops = operands(args)
        if base in ("sed", "perl") and any(a == "-i" or a.startswith("-i") or a.startswith("--in-place")
                                           for a in args):
            targets += ops
        elif base in ("rm", "unlink", "truncate", "shred", "tee", "mv"):
            targets += ops
        elif base in ("cp", "install", "ln", "rsync") and ops:
            targets.append(ops[-1])
            targets += [os.path.join(ops[-1], os.path.basename(src)) for src in ops[:-1]]
        elif base == "dd":
            targets += [a[3:] for a in args if a.startswith("of=")]
        elif base == "git":
            verb, gargs = git_verb(words)
            if verb in ("mv", "rm", "restore"):
                targets += operands(gargs)
            elif verb == "checkout" and "--" in gargs:
                targets += gargs[gargs.index("--") + 1:]
    resolved = []
    for t in targets:
        if not t or t == "/dev/null" or "$(" in t or "`" in t:
            continue
        resolved.append(os.path.normpath(os.path.join(where, os.path.expanduser(t))))
    return resolved


def applied_migrations_under(root, rel):
    """Migration files at or under `rel` that already exist on origin/main."""
    rc, out = git(root, "ls-tree", "-r", "--name-only", "origin/main", "--", rel.rstrip("/") or ".")
    if rc != 0:
        return []
    return [p for p in out.splitlines() if p.startswith(MIGRATIONS_DIR) and p.endswith(".py")]


def classify_write(root, abspath):
    """(decision, reason) for a write to `abspath`: ('deny'|'ask'|None, text)."""
    norm = abspath.replace(os.sep, "/")
    if "/nexotec-gates/" in norm or norm.endswith("/nexotec-gates"):
        return "deny", "The gate state is written only by the hooks and `scripts/dev/gate`."
    for suffix, why in GENERATED_SUFFIXES.items():
        if norm.endswith(suffix):
            return "deny", why
    if not root:
        return None, ""
    rootn = os.path.normpath(root)
    if not (abspath == rootn or abspath.startswith(rootn + os.sep)):
        return None, ""
    rel = os.path.relpath(abspath, rootn).replace(os.sep, "/")
    if rel in GENERATED_FILES:
        return "deny", GENERATED_FILES[rel]
    if rel == "." or rel == "alembic" or rel.startswith("alembic/"):
        applied = applied_migrations_under(root, rel)
        if applied:
            return "deny", (
                "{} is (or contains) a migration that is already on main, and applied migrations are "
                "immutable: every environment that ran it would silently diverge. Write a new migration "
                "that corrects it instead (see .claude/rules/migrations.md).".format(applied[0] if rel == "."
                                                                                     else rel))
    if rel in PRESERVED_FILES or PRESERVED_PATTERN.match(rel):
        return "ask", ("{} is on CLAUDE.md's preserve-verbatim list - a change needs Anto's approval."
                       .format(rel))
    if rel in SETUP_FILES or rel.startswith(SETUP_PREFIXES):
        return "ask", ("{} is part of the gates themselves (.claude/README.md) - a change needs Anto's "
                       "approval.".format(rel))
    return None, ""


# Permission modes in which a hook's "ask" is put to the person at the keyboard. In every other mode -
# auto, bypassPermissions, dontAsk, one added later, or none reported - an "ask" may be settled without
# Anto ever seeing it (KAN-255: an edit to app/core/auth.py applied in an auto-mode session), so it is
# denied instead. Anto's ruling (KAN-255, option A): unattended sessions never touch these files.
ATTENDED_MODES = ("default", "acceptEdits", "plan")


def settle_ask(data, decision, why):
    """The decision a PreToolUse hook emits: an "ask" outside an attended mode becomes a "deny"."""
    if decision != "ask" or data.get("permission_mode") in ATTENDED_MODES:
        return decision, why
    return "deny", (why + " This session runs in permission mode `{}`, where the approval prompt may never "
                    "reach Anto, so the change is refused. Stop, ask Anto in chat, and leave the edit to an "
                    "attended session (default, acceptEdits or plan mode)."
                    .format(data.get("permission_mode") or "unknown"))


INTERPRETERS = ("python", "python3", "node", "ruby", "perl")

# --------------------------------------------------------------------------
# The reviewer agent is read-only
# --------------------------------------------------------------------------

REVIEWER_GIT_READ = ("status", "diff", "log", "show", "blame", "grep", "ls-files", "ls-tree", "rev-parse",
                     "rev-list", "merge-base", "cat-file", "describe", "shortlog", "show-ref",
                     "for-each-ref", "name-rev", "fetch", "worktree")


def reviewer_problem(words, redirects, where, root):
    """Why the read-only reviewer may not run this simple command, or None.
    Scratch files outside the checkout (/tmp) are fine; nothing inside it may change."""
    rootn = os.path.normpath(root)
    inside = [t for t in write_targets(words, redirects, where) if t == rootn or t.startswith(rootn + os.sep)]
    if inside:
        return "it writes {}".format(os.path.relpath(inside[0], rootn))
    if not words:
        return None
    base, args = os.path.basename(words[0]), words[1:]
    if base == "git":
        verb, gargs = git_verb(words)
        if verb == "worktree" and operands(gargs)[:1] not in (["list"], []):
            return "it changes worktrees"
        if verb == "branch" and any(a in ("-d", "-D", "-m", "-M", "-c", "-C", "--delete", "--move", "--copy",
                                           "-f", "--force", "-u", "--set-upstream-to") for a in gargs):
            return "it changes branches"
        if verb not in REVIEWER_GIT_READ + ("branch", "config", "remote", "tag", "stash"):
            return "`git {}` changes the repository".format(verb)
        if verb == "stash" and operands(gargs)[:1] not in (["list"], ["show"]):
            return "`git stash` changes the working copy"
        if verb == "tag" and operands(gargs):
            return "it creates a tag"
        if verb == "config" and not any(a in ("--get", "--get-all", "--list", "-l") for a in gargs):
            return "it changes git config"
        return None
    if base == "ruff" and (any(a.startswith(("--fix", "--unsafe-fixes", "--add-noqa")) for a in args)
                           or (operands(args)[:1] == ["format"] and not {"--check", "--diff"} & set(args))):
        return "it rewrites source files"
    if base in ("oxlint", "eslint", "prettier") and any(a.startswith(("--fix", "--write")) for a in args):
        return "it rewrites source files"
    if "vitest" in words[:3] and ("-u" in args or "--update" in args):
        return "it rewrites snapshots"
    if base == "alembic" and operands(args)[:1] in (["revision"], ["merge"], ["edit"]):
        return "it creates migration files"
    if base in ("npm", "pnpm", "yarn") and operands(args)[:1] in (["ci"], ["install"], ["i"], ["uninstall"],
                                                                   ["update"], ["pkg"]):
        return "it changes installed packages"
    if base in ("pip", "pip3", "uv") or (base.startswith("python") and "pip" in args[:2]):
        return "it changes installed packages"
    if any(t.endswith(("scripts/dev/check", "scripts/dev/bootstrap")) for t in words[:2]):
        return "it records evidence or rebuilds the environment - the builder runs it"
    gate_cmd = gate_cli_command(words)
    if gate_cmd not in (None, "status", "review-scope"):
        return "only `scripts/dev/gate status` and `scripts/dev/gate review-scope` are read-only"
    if base == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok", "-okdir") for a in args):
        return "`find` may modify files"
    if base in ("make", "docker", "chmod", "chown", "mkdir", "touch", "patch"):
        return "it changes files or services"
    return None


# --------------------------------------------------------------------------
# SessionStart
# --------------------------------------------------------------------------


def venv_status(root):
    """Return (ok, message). ok means: this checkout has its own .venv whose
    editable install of the project points at this checkout."""
    venv = os.path.join(root, ".venv")
    if not os.path.isdir(venv):
        return False, ("no .venv in this checkout yet - run `scripts/dev/bootstrap`; its tools are on this "
                       "session's PATH as soon as it finishes")
    site = glob.glob(os.path.join(venv, "lib", "python3*", "site-packages"))
    sources = []
    for sp in site:
        for finder in glob.glob(os.path.join(sp, "__editable__*finder.py")):
            try:
                with open(finder) as fh:
                    m = re.search(r"'app':\s*'([^']+)'", fh.read())
            except OSError:
                m = None
            if m:
                sources.append(os.path.dirname(m.group(1)))
        for pth in glob.glob(os.path.join(sp, "__editable__*.pth")) + glob.glob(os.path.join(sp, "_dms_platform*.pth")):
            try:
                with open(pth) as fh:
                    first = fh.read().strip().splitlines()[0]
            except (OSError, IndexError):
                continue
            if first and not first.startswith("import") and os.path.isdir(first):
                sources.append(first)
    if not sources:
        return False, "own .venv, but the project is not installed in it - run `scripts/dev/bootstrap`"
    if any(os.path.normpath(src) != os.path.normpath(root) for src in sources):
        return False, (".venv's editable install points at {} - imports would run THAT checkout's code; "
                       "run `scripts/dev/bootstrap`".format(sources[0]))
    return True, "own .venv, installed from this checkout, first on PATH"


DARWIN_LIBRARY_PATH = "/opt/homebrew/lib:/usr/local/lib:{}/lib:/usr/lib".format(os.path.expanduser("~"))


def pango_loads(root):
    """True when this checkout's .venv can import WeasyPrint (it needs the Pango library)."""
    py = os.path.join(root, ".venv", "bin", "python")
    if not os.path.exists(py):
        return True  # nothing to check yet; bootstrap checks it
    env = dict(os.environ)
    if sys.platform == "darwin":
        env["DYLD_FALLBACK_LIBRARY_PATH"] = DARWIN_LIBRARY_PATH
    rc, _ = run([py, "-c", "import weasyprint"], cwd=root, env=env, timeout=20)
    return rc == 0


def ensure_test_db(root):
    """Create this checkout's test database if Postgres is up. Uses the
    checkout's own .venv (psycopg is a project dependency)."""
    py = os.path.join(root, ".venv", "bin", "python")
    if not (os.path.exists(py) and postgres_reachable()):
        return False
    code = (
        "import sys, psycopg\n"
        "name = sys.argv[1]\n"
        "with psycopg.connect('postgresql://dms:dms@{h}:{p}/postgres', autocommit=True, connect_timeout=3) as c:\n"
        "    if not c.execute('SELECT 1 FROM pg_database WHERE datname = %s', (name,)).fetchone():\n"
        "        c.execute('CREATE DATABASE \"' + name + '\"')\n"
    ).format(h=PG_HOST, p=PG_PORT)
    rc, _ = run([py, "-c", code, test_db_name(root)], cwd=root, timeout=20)
    return rc == 0


def cmd_session_start(data):
    root = root_from(data)
    if not root:
        return
    lines = []
    wt = is_worktree(root)
    cloud = os.environ.get("CLAUDE_CODE_REMOTE") == "true"
    branch = current_branch(root) or "(detached HEAD)"
    lines.append("Nexotec session status (from .claude/hooks - trust this over memory):")
    if wt:
        where = "worktree " + os.path.basename(root)
    elif cloud:
        where = "CLOUD SESSION (its own copy of the repository)"
    else:
        where = "MAIN CHECKOUT"
    rc, counts = git(root, "rev-list", "--left-right", "--count", "HEAD...origin/main")
    rel = ""
    if rc == 0 and counts:
        ahead, behind = counts.split()
        rel = " - {} ahead / {} behind origin/main".format(ahead, behind)
    lines.append("- Checkout: {} on branch `{}`{}".format(where, branch, rel))
    if cloud and not wt:
        lines.append(
            "  A cloud session works on its own fresh copy, as isolated as a worktree: ticket work "
            "runs here. Run `scripts/dev/bootstrap` first - it also starts this machine's Postgres. "
            "There is no desktop preview; screenshots come from a headless browser (/ticket says how)."
        )
    elif not wt:
        lines.append(
            "  The main checkout stays on `main` and clean; code work happens in a "
            "worktree session. Do not edit or commit here unless Anto asks for it."
        )
    ok, msg = venv_status(root)
    lines.append("- Python: " + msg)

    # Always the same three lines: the paths are fixed per checkout, so a .venv or a
    # database that bootstrap creates later in this session is picked up without a restart.
    venv = os.path.join(root, ".venv")
    env_lines = ['export VIRTUAL_ENV="{}"'.format(venv),
                 'export PATH="{}:$PATH"'.format(os.path.join(venv, "bin")),
                 'export DMS_TEST_DATABASE_URL="{}"'.format(test_db_url(root))]
    if sys.platform == "darwin":
        # WeasyPrint loads Pango by name; macOS does not search Homebrew's lib folders.
        env_lines.append('export DYLD_FALLBACK_LIBRARY_PATH="{}"'.format(DARWIN_LIBRARY_PATH))
    if ok and not pango_loads(root):
        lines.append("- PDF library: WeasyPrint cannot load Pango, so nothing that imports the app runs "
                     "(tests, migrations, the API) - install it (`brew install pango` on macOS), then "
                     "`scripts/dev/bootstrap`")
    name = test_db_name(root)
    if postgres_reachable():
        if ensure_test_db(root):
            lines.append("- Tests: `pytest` runs on Postgres, the lane of record (database `{}`, exported for "
                         "this session)".format(name))
        else:
            lines.append("- Tests: `pytest` is pointed at Postgres database `{}`, which `scripts/dev/bootstrap` "
                         "creates - run it before testing".format(name))
    else:
        start = ("run `scripts/dev/bootstrap`, which starts this machine's Postgres" if cloud else
                 "start Docker Desktop, then run `scripts/dev/bootstrap`")
        lines.append("- Tests: Postgres is not reachable on localhost:5432 - {}. Until then `pytest` fails (it is "
                     "pointed at `{}`); the SQLite fast lane (`scripts/dev/check --fast`) never counts as "
                     "verification (ADR-011)".format(start, name))
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if env_file:
        try:
            with open(env_file, "a") as fh:
                fh.write("\n".join(env_lines) + "\n")
        except OSError:
            pass
    lines.append("- GitHub CLI: " + ("available" if shutil.which("gh") else
                                     "`gh` NOT found - install it (`brew install gh`, `gh auth login`); "
                                     "the PR steps and the stop gate need it"))
    state = load_state(root)
    ticket = state.get("ticket")
    if ticket:
        lines.append("- Active ticket: {} ({}), started {}".format(
            ticket.get("id"), ticket.get("url") or ticket.get("page_id"), ticket.get("started")))
        lines.append("  Gates: run `scripts/dev/gate status` to see what push and hand-over still need.")
    else:
        lines.append("- Active ticket: none (ticket work starts with `/ticket KAN-n`)")
    open_items = handover_open(state)
    if open_items:
        lines.append("- OPEN HAND-OVER since {}: {} still missing for the pushed code - finish these before "
                     "new work (`scripts/dev/gate status`).".format(
                         open_items.get("at"), ", ".join(open_items.get("items", []))))
    sys.stdout.write("\n".join(lines) + "\n")


def cmd_session_sync(data):
    """Async: keep the main checkout's `main` current so hooks and new
    worktrees start from the latest policy. Only fast-forwards a clean main."""
    root = root_from(data)
    if not root:
        return
    main = main_checkout(root)
    if not main or not os.path.isdir(main):
        return
    git(main, "fetch", "--quiet", "origin", "main", timeout=60)
    if current_branch(main) not in MAIN_BRANCHES:
        return
    rc, dirty = git(main, "status", "--porcelain", "--untracked-files=no")
    if rc == 0 and not dirty:
        git(main, "merge", "--ff-only", "--quiet", "origin/main", timeout=60)


# --------------------------------------------------------------------------
# PreToolUse: Bash
# --------------------------------------------------------------------------

VENV_TOOLS = ("python", "python3", "pytest", "alembic", "mypy", "lint-imports", "uvicorn")


def _line_count(value):
    """`200`, `+5`, `-5` -> (int, sign) for a head/tail count; None when it is no count."""
    m = re.match(r"^([+-]?)(\d+)$", value or "")
    return (int(m.group(2)), m.group(1)) if m else None


def _prints_too_much(verb, args):
    """Whether `verb args` prints more than WHOLE_READ_MAX_LINES lines of its file argument (KAN-618)."""
    if verb in ("cat", "less", "more", "bat", "batcat", "nl", "tac"):
        return True
    if verb in ("head", "tail"):
        count, i = (10, ""), 0
        while i < len(args):
            a = args[i]
            if a in ("-n", "--lines") and i + 1 < len(args):
                count, i = _line_count(args[i + 1]) or (WHOLE_READ_MAX_LINES + 1, ""), i + 2
                continue
            if a.startswith("--lines="):
                count = _line_count(a.split("=", 1)[1]) or (WHOLE_READ_MAX_LINES + 1, "")
            elif a.startswith("-n") and len(a) > 2:
                count = _line_count(a[2:]) or (WHOLE_READ_MAX_LINES + 1, "")
            elif re.match(r"^-\d+$", a):
                count = (int(a[1:]), "")
            elif a.startswith(("-c", "--bytes")):
                return True  # bytes, not lines: no line bound
            i += 1
        n, sign = count
        return (verb == "head" and sign == "-") or (verb == "tail" and sign == "+") or n > WHOLE_READ_MAX_LINES
    if verb == "sed":
        if any(a.startswith(("-i", "--in-place")) for a in args):
            return False  # a write, judged as one
        quiet = any(a in ("--quiet", "--silent") or re.match(r"^-[a-zA-Z]*n[a-zA-Z]*$", a) for a in args)
        if not quiet:
            return True  # without -n, sed prints every input line
        scripts = [args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-e", "--expression")]
        if not scripts:
            scripts = [a for a in args if not a.startswith("-")][:1]
        for script in scripts:
            for part in re.split(r"[;\n]", script):
                part = part.strip()
                if not part.endswith("p"):
                    continue
                address = part[:-1].strip()
                if not address or "~" in address or "!" in address:
                    return True  # a bare `p`, a step (`1~1p`) or a negation (`$!p`) has no line bound
                m = re.match(r"^(\d+)\s*,\s*(\d+|\$|\+\d+)$", address)
                if m:
                    start, end = int(m.group(1)), m.group(2)
                    if end == "$":
                        return True
                    span = int(end[1:]) + 1 if end.startswith("+") else int(end) - start + 1
                    if span > WHOLE_READ_MAX_LINES:
                        return True
                elif "," in address:
                    return True  # a range bounded by a pattern has no line bound
        return False
    return False


def whole_read_problem(words, redirects, where, root):
    """KAN-618: a shell command that would print a WHOLE_READ_GUARDED file whole."""
    if not (words and root):
        return None
    verb = os.path.basename(words[0])
    rootn = os.path.normpath(root)
    hits = []
    for tok in words[1:] + [t for op, t in redirects if op == "<"]:
        if tok.startswith("-"):
            continue
        rel = os.path.relpath(os.path.normpath(os.path.join(where, os.path.expanduser(tok))), rootn)
        if rel.replace(os.sep, "/") in WHOLE_READ_GUARDED:
            hits.append(rel.replace(os.sep, "/"))
    if not hits or not _prints_too_much(verb, words[1:]):
        return None
    return ("`{}` would print `{}` ({}) whole, or more than {} lines of it. Look a schema or operation up with "
            "`scripts/dev/api-type <SchemaName | /v1/path>`, or find the lines with `grep -n` and print at most "
            "{} of them (`sed -n 'A,Bp'`).".format(verb, hits[0], WHOLE_READ_GUARDED[hits[0]],
                                                   WHOLE_READ_MAX_LINES, WHOLE_READ_MAX_LINES))


def whole_read_tool_problem(root, tool_input):
    """KAN-618: the Read tool on a WHOLE_READ_GUARDED file needs a `limit` of at most
    WHOLE_READ_MAX_LINES. Not a `Read(...)` deny rule in settings.json: Claude Code applies such a
    rule to `grep` and Grep on the same path too, and searching the file must keep working."""
    path = (tool_input or {}).get("file_path") or ""
    if not (root and path):
        return None
    rel = os.path.relpath(os.path.normpath(path), os.path.normpath(root)).replace(os.sep, "/")
    if rel not in WHOLE_READ_GUARDED:
        return None
    limit = (tool_input or {}).get("limit")
    if isinstance(limit, int) and 0 < limit <= WHOLE_READ_MAX_LINES:
        return None
    return ("`{}` is {} - never read it whole. Look a schema or operation up with `scripts/dev/api-type "
            "<SchemaName | /v1/path>`, or find the lines with `grep -n` and Read them with an `offset` and a "
            "`limit` of at most {}.".format(rel, WHOLE_READ_GUARDED[rel], WHOLE_READ_MAX_LINES))


def cmd_pre_read(data):
    path = ((data.get("tool_input") or {}).get("file_path") or "")
    if not path.replace(os.sep, "/").endswith(tuple(WHOLE_READ_GUARDED)):
        return  # every other Read: no git call, nothing to say
    root = toplevel(os.path.dirname(path))
    problem = whole_read_tool_problem(root, data.get("tool_input"))
    if problem:
        pre_decision("deny", problem)


def cmd_pre_bash(data):
    start = data.get("cwd") or os.getcwd()
    session_root = toplevel(start)
    if not session_root:
        return
    command = (data.get("tool_input") or {}).get("command", "")
    reviewer = data.get("agent_type") == "reviewer"
    denies, asks, notes = [], [], []
    moved = None  # the tree-changing git command seen earlier in this command line
    evidence_roots = []  # checkouts whose push may need an evidence commit first (KAN-647)
    for words, redirects, where in located_commands(command, start):
        root = toplevel(where) or session_root
        for target in write_targets(words, redirects, where):
            decision, why = settle_ask(data, *classify_write(root, target))
            if decision == "deny":
                denies.append(why)
            elif decision == "ask":
                asks.append(why)
        if words and os.path.basename(words[0]).startswith(INTERPRETERS) and \
                any("nexotec-gates" in w for w in words[1:]):
            denies.append("The gate state is written only by the hooks and `scripts/dev/gate`; read it with "
                          "`scripts/dev/gate status`.")
        problem = whole_read_problem(words, redirects, where, root)
        if problem:
            denies.append(problem)
        if types_check_status(words):
            denies.append("The `{}` commit status is posted by the push hook from the recorded check result, "
                          "never by a command typed here (KAN-625). Run `scripts/dev/check`, then push."
                          .format(CHECK_STATUS_CONTEXT))
        if types_evidence_commit(words, where, redirects) or (
                EVIDENCE_MARKER in command and git_verb(words)[0] in ("commit", "commit-tree")):  # `printf … | git commit-tree -F -`
            denies.append("A commit carrying `{}` is made only by the push hook, from the recorded check result "
                          "(KAN-647). Run `scripts/dev/check`, then push.".format(EVIDENCE_MARKER))
        if reviewer:
            problem = reviewer_problem(words, redirects, where, root)
            if problem:
                denies.append("The reviewer is read-only and this command is not: {} (`{}`). Report what you "
                              "would change as a finding instead.".format(problem, " ".join(words[:4])))
        if not words:
            continue
        verb, args = git_verb(words)
        if verb == "push":
            if moved:
                denies.append("`git push` follows `git {}` in the same command, so it would push a tree the "
                              "gate never saw. Run `git push` as its own command once `scripts/dev/check` has "
                              "passed for what you are pushing.".format(moved))
            denies.extend(push_problems(root, args, notes))
            if push_mode(args) == "push" and "--tags" not in args and \
                    all(src == "HEAD" for src, _dst in push_pairs(args, current_branch(root))):
                evidence_roots.append(root)
        elif verb == "commit":
            if "--no-verify" in args or "n" in option_letters(args, takes_value="mFcCt", takes_attached="Su"):
                denies.append("`git commit --no-verify` is never allowed; fix what the hook reports instead.")
            if current_branch(root) in MAIN_BRANCHES:
                denies.append("Never commit on `main`. Create a branch first, e.g. "
                              "`git switch -c kan-<n>-<short-slug>`.")
        if verb in TREE_CHANGING_GIT:
            moved = verb
        gate_cmd = gate_cli_command(words)
        if gate_cmd == "stamp-checks":
            denies.append("Only `scripts/dev/check` records a check result - run the check.")
        elif gate_cmd == "waive" and not any(t.endswith("scripts/dev/gate") for t in words[:3]):
            denies.append("Waivers go through `scripts/dev/gate waive <gate> \"<reason>\"`, which asks Anto.")
        # Another checkout's Python environment runs that checkout's code - and a bare
        # tool in another checkout runs THIS session's environment against it.
        here = root
        executed = words[:2] if words[0] in ("source", ".") else words[:1]
        for tok in executed:
            if ".venv/" in tok:
                path = os.path.normpath(os.path.join(where, os.path.expanduser(tok)))
                own = os.path.normpath(os.path.join(here, ".venv"))
                if not (path == own or path.startswith(own + os.sep)):
                    denies.append(
                        "`{}` belongs to another checkout's .venv, whose editable install imports THAT "
                        "checkout's `app` package - you would run the wrong code. In {} use its own "
                        "`.venv/bin/...` (run `scripts/dev/bootstrap` there once).".format(tok, here))
                    break
        if os.path.normpath(here) != os.path.normpath(session_root) and words[0] in VENV_TOOLS:
            denies.append(
                "`{}` runs in {}, but the bare command resolves to this session's .venv, which imports this "
                "session's `app` - the wrong code. Call that checkout's own `.venv/bin/{}` instead (run "
                "`scripts/dev/bootstrap` there once).".format(words[0], here, words[0]))
    if denies:
        pre_decision("deny", "Blocked by the Nexotec gates:\n- " + "\n- ".join(dict.fromkeys(denies)))
    elif asks:
        pre_decision("ask", "\n".join(dict.fromkeys(asks)))
    else:
        # KAN-647: never for a push the gates stop or ask about. The harness's own permission prompt
        # (`git push*` is an `ask` rule) comes after this hook, and a push can still fail: the
        # PostToolUse hook drops the commit again when the push did not land; a declined prompt
        # runs no PostToolUse, and the commit then waits, unpushed, for the next push (never two).
        for root in dict.fromkeys(evidence_roots):
            add_evidence_commit(root)
        if notes:
            # Said, not decided: no permissionDecision, so the push's own permission prompt still asks.
            emit({"systemMessage": "\n".join(dict.fromkeys(notes))})


# --------------------------------------------------------------------------
# PreToolUse: Edit / Write / MultiEdit / NotebookEdit
# --------------------------------------------------------------------------


def cmd_pre_edit(data):
    ti = data.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if not path:
        return
    if data.get("agent_type") == "reviewer":
        pre_decision("deny", "The reviewer is read-only - report the change as a finding instead.")
        return
    root = root_from(data)
    abspath = os.path.normpath(os.path.join(data.get("cwd") or root or os.getcwd(), os.path.expanduser(path)))
    decision, why = settle_ask(data, *classify_write(root, abspath))
    if decision:
        pre_decision(decision, why)


# --------------------------------------------------------------------------
# PreToolUse: Notion connector tools
# --------------------------------------------------------------------------


def notion_tool(tool_name):
    """'mcp__claude_ai_Notion__notion-update-page' -> 'notion-update-page'."""
    if "notion" not in (tool_name or "").lower():
        return None
    return tool_name.split("__")[-1].lower()


NOTION_CREATED_MAX = 200  # pages a session created, kept for the replace_content rule (KAN-685)


def notion_decision(data, state=None):
    """(decision, reason) for a Notion tool call, or None for a read.

    Every Notion write is allowed without Anto's click (Anto, 2026-10-11:
    "you are free to update Notion, I don't need to allow it every time").
    The reviewer stays read-only. What a write to a page other than the
    active ticket changed is said in the reply and the PR, not in a prompt.

    One write shape is refused (KAN-685): `notion-update-page` with `replace_content` writes the
    whole page from what this session fetched, so on a page parallel sessions edit (the Gap
    Analysis, a PRD, the Target Architecture) it silently removes an edit made since. It stays
    allowed on the session's active ticket and on pages this session created."""
    tool = notion_tool(data.get("tool_name"))
    if not tool or tool.startswith(NOTION_READ_PREFIXES):
        return None
    if data.get("agent_type") == "reviewer":
        return "deny", "The reviewer is read-only - report the Notion change as a finding instead."
    ti = data.get("tool_input") or {}
    if tool == "notion-update-page" and str(ti.get("command", "")).lower() == "replace_content":
        state = state or {}
        page = notion_id(ti.get("page_id"))
        own = {c.get("page") for c in state.get("notion_created") or [] if c.get("session") == data.get("session_id")}
        if not page or (page != (state.get("ticket") or {}).get("page_id") and page not in own):
            return "deny", (
                "`replace_content` rewrites the whole page from what this session fetched, so an edit another "
                "session made since would silently disappear (KAN-685). Edit the page in place instead: "
                "`update_content` (search and replace) or `insert_content`. When an `old_str` no longer matches, "
                "fetch the page again and apply the edit to what is there - never replace the page. "
                "`replace_content` is allowed only on the active ticket (`scripts/dev/gate start`) and on pages "
                "this session created.")
    return "allow", "Notion write ({}) - Anto allows every Notion write (2026-10-11)".format(tool)


def cmd_pre_notion(data):
    root = root_from(data)
    decision = notion_decision(data, load_state(root) if root else {})
    if decision:
        pre_decision(*decision)


# --------------------------------------------------------------------------
# PreToolUse: Render's connector (KAN-686)
# --------------------------------------------------------------------------


def render_decision(tool_name):
    """(decision, reason) for a Render connector tool, or None. Sessions read staging; nothing a session
    does changes Render - a deploy is triggered by a merge (Anto, 2026-11-13). Reads are left to
    settings.json's allow list; every other tool is denied, whatever the server is called."""
    server, _, tool = (tool_name or "").rpartition("__")
    if "render" not in server.lower() or tool in RENDER_READ_TOOLS:
        return None
    return "deny", ("`{}` is not one of Render's read tools. Sessions read staging through the connector - "
                    "services, deploys, events, logs, metrics, Postgres and read-only SQL - and never change it: "
                    "a deploy is triggered by a merge, a data correction goes through the API as a platform_admin "
                    "(KAN-686, Anto 2026-11-13; `.claude/README.md`, «Staging»).".format(tool or tool_name))


def cmd_pre_render(data):
    decision = render_decision(data.get("tool_name"))
    if decision:
        pre_decision(*decision)


def created_page_ids(response):
    """The ids of the pages a `notion-create-pages` answer reports - `pages[].id` and `pages[].url` only,
    read from its JSON (the answer is a JSON object, or text blocks holding one). Anything else in the
    answer (a parent, an ancestor, a relation target) is not taken: an id taken wrongly would open a
    shared page to `replace_content`, so an answer that cannot be read records nothing (KAN-685)."""
    def objects(value):
        if isinstance(value, dict):
            if value.get("type") == "text" and isinstance(value.get("text"), str):
                yield from objects(value["text"])
            else:
                yield value
        elif isinstance(value, list):
            for item in value:
                yield from objects(item)
        elif isinstance(value, str):
            try:
                yield from objects(json.loads(value))
            except ValueError:
                return
    found = set()
    for obj in objects(response):
        pages = obj.get("pages")
        for page in pages if isinstance(pages, list) else []:
            if isinstance(page, dict):
                found.update(filter(None, (notion_id(page.get("id")), notion_id(page.get("url")))))
    return found


def record_created_pages(root, data):
    """After `notion-create-pages`: remember the pages it made, for this session (KAN-685)."""
    made = created_page_ids(data.get("tool_response")) - KANBAN_IDS
    if not made:
        return

    def remember(st):
        created = st.setdefault("notion_created", [])
        created.extend({"page": page, "session": data.get("session_id"), "at": now()} for page in sorted(made))
        del created[:-NOTION_CREATED_MAX]
    update_state(root, remember)


# --------------------------------------------------------------------------
# PostToolUse: record evidence
# --------------------------------------------------------------------------


def is_screenshot(tool_name, tool_input):
    """A screenshot of the running app: the desktop preview / built-in browser, or a
    Playwright capture of localhost. Anything else is registered with `gate evidence`."""
    name = (tool_name or "").lower()
    ti = tool_input if isinstance(tool_input, dict) else {}
    if any(p in name for p in PREVIEW_TOOLS):
        if "screenshot" in name:
            return True
        if name.endswith("computer") and str(ti.get("action", "")).lower() in ("screenshot", "zoom"):
            return True
        if name.endswith("browser_batch"):
            for item in ti.get("actions") or []:
                if isinstance(item, dict) and str(item.get("name", "")).lower().endswith("computer") and \
                        str((item.get("input") or {}).get("action", "")).lower() in ("screenshot", "zoom"):
                    return True
        return False
    if name == "bash":
        return bool(re.search(r"playwright\S*\s+screenshot\b.*https?://(localhost|127\.0\.0\.1)[:/]",
                              str(ti.get("command", ""))))
    return False


def cmd_post_tool(data):
    root = root_from(data)
    if not root:
        return
    name = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return  # evidence is keyed by tree hash; edits need no bookkeeping
    if is_screenshot(name, ti):
        append_event(root, "screenshot", tool=name, tree=fingerprint(root))
        return
    tool = notion_tool(name)
    if tool == "notion-create-pages":
        record_created_pages(root, data)
        return
    if tool in NOTION_TICKET_WRITES:
        ticket = load_state(root).get("ticket") or {}
        if ticket.get("page_id") and notion_id(ti.get("page_id")) == ticket.get("page_id"):
            append_event(root, "notion", tool=tool, ticket=ticket.get("id"))
        return
    if name == "Bash":
        located = list(located_commands(ti.get("command", ""), data.get("cwd") or root))
        merged = [toplevel(where) or root for words, _r, where in located
                  if git_verb(words)[0] in ("merge", "pull", "commit")]
        # KAN-683: a merge of main (or the commit that concludes a conflicted one) - carry the evidence
        # when the ticket's change is unchanged, or say that the resolution needs review. Said together
        # with the push's notes below: a hook emits one answer.
        context = [carry_note(carry_merge_evidence(merged[-1]))] if merged else []
        for words, _redirects, where in located:
            verb, args = git_verb(words)
            if verb == "push" and push_mode(args) == "push":
                pushed = toplevel(where) or root
                if push_landed(pushed, args):
                    append_event(pushed, "push", session=data.get("session_id"), head=head_commit(pushed),
                                 tree=head_tree(pushed), branch=current_branch(pushed))
                    # KAN-625: the check result goes onto each pushed commit, from the recorded evidence.
                    context += [check_status_note(post_check_status(pushed, src), pushed)
                                for src, _dst in push_pairs(args, current_branch(pushed)) if src]
                else:
                    # KAN-647: no stray empty commit after a failed push - only for a push to a named remote,
                    # whose tracking ref says whether it landed (a push to a URL updates none).
                    remote = (push_positionals(args) or ["origin"])[0]
                    if git(pushed, "remote", "get-url", remote)[0] == 0 and "://" not in remote and ":" not in remote:
                        drop_unpushed_evidence_commit(pushed)
                break
        if any(context):
            emit({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                         "additionalContext": "\n".join(n for n in context if n)}})


# --------------------------------------------------------------------------
# The check result as a commit status on the PR (KAN-625)
# --------------------------------------------------------------------------

# GitHub's two endpoints that write a check result onto a commit: `repos/<o>/<r>/statuses/<sha>`
# and `repos/<o>/<r>/check-runs`. Both only write; reading goes through `commits/<ref>/statuses`
# and `commits/<ref>/check-runs`, which this does not match.
STATUS_WRITE_PATH = re.compile(r"(?:^|/)repos/[^/]+/[^/]+/(?:statuses/[^/?]+|check-runs)/?(?:\?|$)")


def types_check_status(words):
    """True for a typed command that would write a check result onto a commit itself: a `gh`,
    `curl` or similar call to a commit-status or check-run write path - whatever the context,
    and however the fields are passed (`-f k=v`, `-fk=v`, `--input file`, a variable) - or an
    interpreter one-liner naming the `nexotec/check` context. Reading statuses, searching for the
    name and naming it in a PR body stay allowed. A path built in a variable or a script file
    is not seen: this is a rail against the plain mistake, not a boundary (ADR-129)."""
    if not words:
        return False
    base = os.path.basename(words[0])
    if base.startswith(INTERPRETERS):
        return any(CHECK_STATUS_CONTEXT in w for w in words[1:])
    return base in ("gh", "curl", "wget", "http", "https") and \
        any(STATUS_WRITE_PATH.search(w) for w in words[1:])


def check_status_payload(checks, tree):
    """The `nexotec/check` status for a commit whose tree is `tree`, built from the recorded check
    (`state["checks"]`) - or None when that record is not a pass for exactly this tree. A
    lane-limited pass says which lanes ran («frontend only»), so a reader sees what did not."""
    if not tree or not isinstance(checks, dict) or checks.get("result") != "pass" or checks.get("tree") != tree:
        return None
    lanes = [str(lane) for lane in checks.get("lanes") or []]
    if not lanes:
        scope = "no lane covers this change"
    elif "not_run" not in checks:
        scope = ", ".join(lanes)  # recorded by a check that did not say what it left out
    elif checks["not_run"]:
        scope = ", ".join(lanes) + " only"
    else:
        scope = "all lanes"
    description = "{} · tree {} · {}".format(scope, tree[:12], checks.get("counts") or "no test counts recorded")
    if len(description) > CHECK_STATUS_MAX:
        description = description[:CHECK_STATUS_MAX - 1] + "…"
    return {"state": "success", "context": CHECK_STATUS_CONTEXT, "description": description}


def post_check_status(root, commit):
    """Post `nexotec/check` on `commit` from the recorded evidence and record what happened as a
    `check-status` event: posted, refused (GitHub's or the proxy's own words) or no-evidence. The
    tree is read from the commit here, never taken from a caller. There is no other way to post
    it: a refusal is recorded and reported, not worked around (KAN-625, criterion 4)."""
    rc, sha = git(root, "rev-parse", "--verify", "--quiet", commit + "^{commit}")
    rc2, tree = git(root, "rev-parse", "--verify", "--quiet", commit + "^{tree}")
    if rc != 0 or rc2 != 0:
        return None
    payload = check_status_payload(load_state(root).get("checks"), tree)
    if payload is None:
        event = {"commit": sha, "tree": tree, "result": "no-evidence",
                 "detail": "no recorded pass for tree {}".format(tree[:12])}
    else:
        _data, problem = gh_api(root, "repos/{owner}/{repo}/statuses/" + sha, fields=payload)
        event = {"commit": sha, "tree": tree, "result": "refused" if problem else "posted",
                 "detail": problem or payload["description"]}
    append_event(root, "check-status", **event)
    return event


def check_status_note(event, root=None):
    """What the session is told when the status did not reach GitHub; '' when it did."""
    if not event or event.get("result") == "posted":
        return ""
    carried = evidence_verdict(root, event.get("commit") or "HEAD") if root and event.get("result") == "refused" else None
    if carried and carried.get("state") == "success":
        return ("The `{}` status could not be written from here ({}), so the push carried it as the evidence "
                "commit {}: the check-evidence workflow posts it on the PR (KAN-647). Say so in the PR."
                .format(CHECK_STATUS_CONTEXT, event.get("detail"), event["commit"][:10]))
    return ("The `{}` commit status was NOT posted on {} ({}): {} Say so in the PR and the hand-over; do not "
            "post it any other way (KAN-625).".format(CHECK_STATUS_CONTEXT, (event.get("commit") or "?")[:10],
                                                     event.get("result"), event.get("detail")))


def ensure_check_status(root, state, pushes):
    """At the hand-over: post the status for the last push when no attempt is on record or the
    last one failed for a reason that may pass (a timeout). A refusal with an HTTP status, or a
    push without evidence, is final and is not repeated at every stop."""
    head = (pushes[-1] if pushes else {}).get("head")
    if not head:
        return
    prior = latest(events(state, "check-status"), commit=head)
    if prior and (prior.get("result") != "refused" or "(HTTP " in (prior.get("detail") or "")):
        return
    post_check_status(root, head)


# --------------------------------------------------------------------------
# The check result as an evidence commit, where the status write is refused (KAN-647, ADR-129 (2))
# --------------------------------------------------------------------------

# The trailer that marks a hook-made evidence commit. Only the push hook writes one; a typed
# commit carrying it is refused (`types_evidence_commit`). `.github/workflows/check-evidence.yml`
# turns a verified one into the `nexotec/check` status (`evidence_verdict`).
EVIDENCE_MARKER = "Nexotec-Check-Evidence:"
EVIDENCE_VERSION = "1"
EVIDENCE_FIELD = re.compile(r"^(Nexotec-Check-Evidence|Tree|Lanes|Not-Run|Counts|Recorded): ?(.*)$", re.MULTILINE)


def evidence_message(checks, tree):
    """The message of the evidence commit for `tree`, from the recorded check - or None when that
    record is not a pass for exactly this tree (the same rule as `check_status_payload`)."""
    if check_status_payload(checks, tree) is None:
        return None
    not_run = checks.get("not_run")
    lines = [
        "nexotec/check evidence for tree {} (KAN-647)".format(tree[:12]),
        "",
        "Made by the push hook because this session cannot write the commit status.",
        "Empty on purpose: the check-evidence workflow posts nexotec/check from it.",
        "",
        "{} {}".format(EVIDENCE_MARKER, EVIDENCE_VERSION),
        "Tree: {}".format(tree),
        "Lanes: {}".format(", ".join(str(lane) for lane in checks.get("lanes") or []) or "none"),
        "Not-Run: {}".format("unknown" if not_run is None else (", ".join(str(n) for n in not_run) or "none")),
        "Counts: {}".format(checks.get("counts") or "none recorded"),
        "Recorded: {}".format(checks.get("at") or "unknown"),
    ]
    return "\n".join(lines) + "\n"


def parse_evidence(message):
    """The evidence fields of a commit message (lower-case keys), or None when it carries no marker."""
    if EVIDENCE_MARKER not in (message or ""):
        return None
    return {key.lower(): value.strip() for key, value in EVIDENCE_FIELD.findall(message)}


def commit_facts(root, commit):
    """(sha, tree, [parent trees], message) of `commit`, or None when it is not a commit here."""
    rc, sha = git(root, "rev-parse", "--verify", "--quiet", commit + "^{commit}")
    if rc != 0:
        return None
    rc, out = git(root, "show", "-s", "--format=%T%n%P", sha)
    if rc != 0:
        return None
    tree, _, parents = out.partition("\n")
    parent_trees = []
    for parent in parents.split():
        rc, ptree = git(root, "rev-parse", parent + "^{tree}")
        parent_trees.append(ptree if rc == 0 else None)
    rc, message = git(root, "show", "-s", "--format=%B", sha)
    return sha, tree, parent_trees, message if rc == 0 else ""


def evidence_verdict(root, commit):
    """What the check-evidence workflow posts for `commit`: None (no evidence marker: post nothing),
    or the status payload, `success` or `error` with the reason. Success only for one parent, the
    same tree as that parent (an empty commit) and the tree its message names."""
    facts = commit_facts(root, commit)
    if facts is None:
        return None
    _sha, tree, parent_trees, message = facts
    fields = parse_evidence(message)
    if fields is None:
        return None

    def error(why):
        return {"state": "error", "context": CHECK_STATUS_CONTEXT,
                "description": ("evidence commit refused: " + why)[:CHECK_STATUS_MAX]}
    if fields.get("nexotec-check-evidence") != EVIDENCE_VERSION:
        return error("unknown evidence version")
    if len(parent_trees) != 1:
        return error("it has {} parents, not one".format(len(parent_trees)))
    if parent_trees[0] != tree:
        return error("it changes files (not empty against its parent)")
    if fields.get("tree") != tree:
        return error("its tree is not the tree its message names")
    lanes, not_run = fields.get("lanes") or "none", fields.get("not-run")
    if lanes == "none":
        scope = "no lane covers this change"
    elif not_run == "unknown":
        scope = lanes
    elif not_run and not_run != "none":
        scope = lanes + " only"
    else:
        scope = "all lanes"
    # The provenance comes first: a long count line is what the 140-character cut takes.
    description = "evidence commit (status write refused) · {} · tree {} · {}".format(
        scope, tree[:12], fields.get("counts") or "no test counts recorded")
    if len(description) > CHECK_STATUS_MAX:
        description = description[:CHECK_STATUS_MAX - 1] + "…"
    return {"state": "success", "context": CHECK_STATUS_CONTEXT, "description": description}


def status_write_refused(state):
    """True where the status write is refused: a cloud session (every one today, ADR-129), or a
    checkout whose last status write GitHub refused with HTTP 403."""
    if os.environ.get("CLAUDE_CODE_REMOTE") == "true":
        return True
    last = latest(events(state, "check-status"))
    return bool(last and last.get("result") == "refused" and "(HTTP 403" in (last.get("detail") or ""))


def add_evidence_commit(root):
    """Before a push from a checkout that cannot write the status: one empty commit on HEAD's tree
    carrying the recorded evidence, on the current branch. Returns its sha, or None when none is
    needed (the status can be written, no pass for HEAD's tree, or HEAD already is the evidence
    commit for that tree) or it could not be made. The tree does not change, so the push gate,
    the review and the screenshot, all bound to the tree, still hold."""
    state = load_state(root)
    branch = current_branch(root)
    if not branch or not status_write_refused(state):
        return None
    facts = commit_facts(root, "HEAD")
    if facts is None:
        return None
    head, tree, parent_trees, message = facts
    message_out = evidence_message(state.get("checks"), tree)
    if message_out is None:
        return None
    fields = parse_evidence(message)
    if fields is not None and fields.get("tree") == tree and parent_trees == [tree]:
        return None  # HEAD already carries it
    try:
        proc = subprocess.run(["git", "commit-tree", tree, "-p", head, "-F", "-"], cwd=root, input=message_out,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    new = proc.stdout.strip()
    if proc.returncode != 0 or not new:
        return None
    if git(root, "update-ref", "-m", "nexotec: check evidence (KAN-647)", "refs/heads/" + branch, new, head)[0] != 0:
        return None
    append_event(root, "evidence-commit", commit=new, parent=head, tree=tree, branch=branch)
    return new


def _option_value(args, i, short, long):
    """The value of `args[i]` when it is `short`/`long` (value in the next word, attached, or after
    `=`), else None."""
    a = args[i]
    if a in (short, long) and i + 1 < len(args):
        return args[i + 1]
    if a.startswith(long + "="):
        return a[len(long) + 1:]
    if a.startswith(short) and len(a) > len(short) and not a.startswith("--"):
        return a[len(short):]
    return None


def types_evidence_commit(words, where, redirects=()):
    """True for a typed command that would make a commit carrying the evidence marker: `git commit`
    or `git commit-tree` with the message inline, in a file (`-F`, a template, a `<` redirect) or
    reused from a commit that carries it (`--amend`, `-C`/`-c`), or an interpreter one-liner naming
    the marker. Only the push hook makes evidence commits (KAN-647). A rail, not a boundary."""
    if not words:
        return False
    if os.path.basename(words[0]).startswith(INTERPRETERS):
        return any(EVIDENCE_MARKER in w for w in words[1:])
    verb, args = git_verb(words)
    if verb not in ("commit", "commit-tree"):
        return False
    if any(EVIDENCE_MARKER in a for a in args):
        return True
    root = toplevel(where) or where
    files = [t for op, t in redirects if op == "<"]
    reused = ["HEAD"] if verb == "commit" and "--amend" in args else []
    for i in range(len(args)):
        for short, long in (("-F", "--file"), ("-t", "--template")):
            value = _option_value(args, i, short, long)
            if value and value != "-":
                files.append(value)
        if verb == "commit":
            for short, long in (("-C", "--reuse-message"), ("-c", "--reedit-message")):
                value = _option_value(args, i, short, long)
                if value:
                    reused.append(value)
    for path in files:
        try:
            with open(os.path.join(where, os.path.expanduser(path)), encoding="utf-8", errors="replace") as fh:
                if EVIDENCE_MARKER in fh.read():
                    return True
        except OSError:
            pass
    for commit in reused:
        rc, message = git(root, "show", "-s", "--format=%B", commit)
        if rc == 0 and EVIDENCE_MARKER in message:
            return True
    return False


def drop_unpushed_evidence_commit(root):
    """After a push that did not land: when HEAD is still the evidence commit the hook made and no
    remote branch holds it, move the branch back to its parent (same tree, nothing lost), so no
    stray empty commit stays behind. Returns True when it did."""
    made = latest(events(load_state(root), "evidence-commit"))
    head, branch = head_commit(root), current_branch(root)
    if not made or not head or made.get("commit") != head or made.get("branch") != branch:
        return False
    rc, holders = git(root, "branch", "-r", "--contains", head)
    if rc != 0 or holders.strip():
        return False
    if git(root, "update-ref", "-m", "nexotec: push did not land, evidence commit dropped (KAN-647)",
           "refs/heads/" + branch, made["parent"], head)[0] != 0:
        return False
    append_event(root, "evidence-dropped", commit=head, parent=made["parent"], branch=branch)
    return True


# --------------------------------------------------------------------------
# Re-review after fixes (KAN-620)
# --------------------------------------------------------------------------


def rereview_full_reason(numstat):
    """Why a range must have a full review, from `git diff --numstat --no-renames` lines, or None
    when the narrow re-review is allowed: it touches no preserved file, no migration and no
    `public.py`, and changes at most REREVIEW_MAX_LINES lines (added plus deleted)."""
    changed = 0
    for line in numstat:
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        if path in PRESERVED_FILES or PRESERVED_PATTERN.match(path):
            return "it touches the preserved file {}".format(path)
        if path.startswith(MIGRATIONS_DIR):
            return "it touches the migration {}".format(path)
        if path.startswith("app/") and os.path.basename(path) == "public.py":
            return "it touches a context's public door, {}".format(path)
        if not (added.isdigit() and deleted.isdigit()):
            return "it changes the binary file {}".format(path)
        changed += int(added) + int(deleted)
    if changed > REREVIEW_MAX_LINES:
        return "it changes {} lines (more than {})".format(changed, REREVIEW_MAX_LINES)
    return None


def rereview_refusal(root, state, previous, commit):
    """Why a re-review of `previous`..`commit` cannot stand, or None. The previous commit must be
    an ancestor of the reviewed one and carry a recorded review of its own (so a chain of
    re-reviews always starts at a full review), and the range must be one the narrow review may
    cover (`rereview_full_reason`)."""
    rc, prev = git(root, "rev-parse", "--verify", "--quiet", previous + "^{commit}")
    if rc != 0:
        return "the previous commit `{}` is not a commit in this checkout".format(previous)
    if git(root, "merge-base", "--is-ancestor", prev, commit)[0] != 0:
        return "the previous commit {} is not an ancestor of the reviewed commit {}".format(prev[:10], commit[:10])
    if not any(r.get("commit") == prev and r.get("verdict") in ("PASS", "FINDINGS") for r in events(state, "review")):
        return "no review of the previous commit {} is on record in this checkout".format(prev[:10])
    base, _merge = rereview_base(root, prev, commit)
    rc, out = git(root, "diff", "--numstat", "--no-renames", base, commit)
    if rc != 0:
        return "the range {}..{} cannot be read".format(prev[:10], commit[:10])
    reason = rereview_full_reason(out.splitlines())
    what = "the range {}..{}".format(prev[:10], commit[:10]) if base == prev else \
        "the resolution of the merge {}".format(commit[:10])
    return "{} needs a full review: {}".format(what, reason) if reason else None


def rereview_base(root, prev, commit):
    """What a re-review of `prev`..`commit` covers (KAN-683): (base, merge facts or None). For a merge of
    main whose first parent is the reviewed commit (or carries its tree), the base is git's own merge
    result - conflict markers included - so the review covers the resolution, not every line main
    gained; otherwise the base is `prev` itself."""
    facts = merge_facts(root, commit)
    rc, prev_tree = git(root, "rev-parse", prev + "^{tree}")
    if facts and rc == 0 and facts["result_tree"] and facts["first_tree"] == prev_tree:
        return facts["result_tree"], facts
    return prev, None


# --------------------------------------------------------------------------
# Evidence across a merge of main (KAN-683)
# --------------------------------------------------------------------------


def merge_facts(root, commit="HEAD"):
    """The two-parent merge at `commit` - or, under a hook-made evidence commit, its parent - as a dict:
    `merge`, `first`, `second` (shas), `first_tree`, `to_tree`, `result_tree` (git's own merge result,
    conflict markers included; None when git cannot reproduce it) and `conflicted`. None for no merge."""
    rc, line = git(root, "rev-list", "--parents", "-n", "1", commit)
    shas = line.split() if rc == 0 else []
    if len(shas) == 2:
        facts = commit_facts(root, shas[0])
        if facts and parse_evidence(facts[3]) is not None and facts[2] == [facts[1]]:
            rc, line = git(root, "rev-list", "--parents", "-n", "1", shas[1])
            shas = line.split() if rc == 0 else []
    if len(shas) != 3:
        return None
    merge, first, second = shas
    rc_t, to_tree = git(root, "rev-parse", merge + "^{tree}")
    rc_f, first_tree = git(root, "rev-parse", first + "^{tree}")
    if rc_t != 0 or rc_f != 0:
        return None
    rc, out = git(root, "merge-tree", "--write-tree", first, second, timeout=60)
    result = out.splitlines()[0].strip() if rc in (0, 1) and out else None
    return {"merge": merge, "first": first, "second": second, "first_tree": first_tree, "to_tree": to_tree,
            "result_tree": result if result and re.match(r"^[0-9a-f]{40,64}$", result) else None,
            "conflicted": rc == 1}


def merge_carry_verdict(root, commit="HEAD"):
    """Whether the review and the screenshot of a merge commit's first parent carry over to it (KAN-683).
    Returns None when `commit` (or, under a hook-made evidence commit, its parent) is no two-parent merge,
    else a dict: `from`/`to` shas and trees, `carry` True/False and the `reason` when False.

    The ticket's own change must be unchanged: git reproduces the merge without conflict
    (`git merge-tree --write-tree`) and its tree is the merge commit's tree (nothing edited in the merge),
    and `git diff origin/main...<commit>` is byte-identical to `git diff origin/main...<first parent>`.
    The check is never carried: the tree is new, so it always runs again."""
    facts = merge_facts(root, commit)
    if facts is None:
        return None
    merge, first, second, to_tree = facts["merge"], facts["first"], facts["second"], facts["to_tree"]
    verdict = {"from": first, "to": merge, "from_tree": facts["first_tree"], "to_tree": to_tree, "carry": False}
    if facts["conflicted"]:
        return dict(verdict, reason="the merge had conflicts")
    if facts["result_tree"] is None:
        return dict(verdict, reason="git could not reproduce the merge (`git merge-tree --write-tree` needs git 2.38)")
    if facts["result_tree"] != to_tree:
        return dict(verdict, reason="the merge commit's tree is not git's own merge result (it was edited)")
    rc_m, _ = git(root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main^{commit}")
    base = "refs/remotes/origin/main" if rc_m == 0 else second
    rc1, before = git(root, "diff", "--binary", "--no-ext-diff", "{}...{}".format(base, first), timeout=60)
    rc2, after = git(root, "diff", "--binary", "--no-ext-diff", "{}...{}".format(base, merge), timeout=60)
    if rc1 != 0 or rc2 != 0:
        return dict(verdict, reason="the three-dot diffs could not be read")
    if before != after:
        return dict(verdict, reason="`git diff origin/main...HEAD` is not the same as before the merge")
    return dict(verdict, carry=True)


def carry_merge_evidence(root):
    """Record the carry-over for HEAD's merge once (KAN-683): on a clean merge with the change unchanged,
    the first parent's reviewer PASS and screenshot (or no-visual note) are recorded again for the merge
    commit's tree, and an `evidence-carried` event names both shas. Otherwise an `evidence-not-carried`
    event says why: the resolution needs a review under KAN-620's `review-scope` rules. Returns that event,
    or None (no merge at HEAD, or already recorded)."""
    verdict = merge_carry_verdict(root)
    if verdict is None or not verdict.get("to_tree"):
        return None
    state = load_state(root)
    if any(e.get("to") == verdict["to"] for e in state.get("events", [])
           if e.get("kind") in ("evidence-carried", "evidence-not-carried")):
        return None
    if not verdict["carry"]:
        event = {"kind": "evidence-not-carried", "at": now(), "from": verdict["from"], "to": verdict["to"],
                 "reason": verdict["reason"]}
        update_state(root, lambda st: st.setdefault("events", []).append(event))
        return event
    from_tree, to_tree = verdict["from_tree"], verdict["to_tree"]
    review = latest([r for r in events(state, "review") if r.get("tree") == from_tree])
    shot = latest([e for e in events(state, "screenshot") + events(state, "novisual") if e.get("tree") == from_tree])
    event = {"kind": "evidence-carried", "at": now(), "from": verdict["from"], "to": verdict["to"],
             "from_tree": from_tree, "to_tree": to_tree,
             "review": bool(review and review.get("verdict") == "PASS"), "screenshot": bool(shot)}

    def record(st):
        new = st.setdefault("events", [])
        if event["review"]:
            new.append({"kind": "review", "at": now(), "verdict": "PASS", "tree": to_tree, "commit": verdict["to"],
                        "mode": "carried", "carried_from": verdict["from"], "agent": review.get("agent"),
                        "session": review.get("session")})
        if event["screenshot"]:
            new.append(dict(shot, at=now(), tree=to_tree, carried_from=verdict["from"]))
        new.append(event)
    update_state(root, record)
    return event


def carry_note(event):
    """What the session is told about a carry-over (KAN-683), or ''."""
    if not event:
        return ""
    if event["kind"] == "evidence-carried":
        carried = [name for name, ok in (("the reviewer's PASS", event["review"]),
                                         ("the screenshot", event["screenshot"])) if ok]
        return ("Merge {} of main: the ticket's change is unchanged, so {} for {} carried over (KAN-683). "
                "Run `scripts/dev/check` - the tree is new.".format(
                    event["to"][:10], " and ".join(carried) or "nothing (none was recorded)", event["from"][:10]))
    return ("Merge {} of main: {}, so no evidence carried over (KAN-683). The resolution needs a review: "
            "`scripts/dev/gate review-scope {}` says whether a re-review may cover it; run "
            "`scripts/dev/check` too.".format(event["to"][:10], event["reason"], event["from"][:10]))


def handback_report(path):
    """The report a subagent handed back through a tool call, read from its own transcript.
    Some harnesses (cloud sessions) deliver the report as the input of a `SubagentHandback`
    tool call and end the subagent with a short "Done", so `last_assistant_message` alone
    never shows the reviewer's `VERDICT:` line (KAN-134)."""
    report = ""
    try:
        with open(os.path.expanduser(path), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "tool_use" not in line:
                    continue
                try:
                    content = (json.loads(line).get("message") or {}).get("content")
                except (ValueError, AttributeError):
                    continue
                for block in content if isinstance(content, list) else []:
                    if not (isinstance(block, dict) and block.get("type") == "tool_use"
                            and "handback" in str(block.get("name", "")).lower()):
                        continue
                    given = block.get("input") or {}
                    text = given.get("message") if isinstance(given.get("message"), str) else \
                        "\n".join(v for v in given.values() if isinstance(v, str))
                    if text:
                        report = text
    except (OSError, TypeError):
        return ""
    return report


def subagent_report(data):
    """A subagent's final report: its last message, or - when that carries no verdict - the
    report it handed back through a tool call (handback_report)."""
    message = data.get("last_assistant_message") or ""
    if REVIEW_VERDICT.search(message):
        return message
    path = data.get("agent_transcript_path")
    if not path and data.get("transcript_path") and data.get("agent_id"):
        path = os.path.join(os.path.splitext(data["transcript_path"])[0], "subagents",
                            "agent-{}.jsonl".format(data["agent_id"]))
    report = handback_report(path) if path else ""
    return report if REVIEW_VERDICT.search(report) else message


def cmd_subagent_stop(data):
    root = root_from(data)
    if not root:
        return
    message = subagent_report(data)
    found = REVIEW_VERDICT.findall(message)
    verdict = found[-1].upper() if found else "NONE"
    commit = tree = None
    named = REVIEWED_COMMIT.findall(message)
    if named:
        rc, commit = git(root, "rev-parse", "--verify", "--quiet", named[-1] + "^{commit}")
        if rc == 0:
            rc, tree = git(root, "rev-parse", commit + "^{tree}")
        if rc != 0:
            commit = tree = None
    if not tree:
        head, fp = head_tree(root), fingerprint(root)
        if head and fp == head:
            tree, commit = head, head_commit(root)
        else:
            tree = fp
            if verdict == "PASS":
                verdict = "UNCOMMITTED"  # it may not have seen the uncommitted part
    # KAN-620: a re-review covers only the fix and the criteria left open, so its verdict stands
    # only on a reviewed ancestor and a range the narrow review may cover. A refused one is
    # recorded as such - never as a PASS, and never as a base for the next re-review.
    extra = {"mode": "full"}
    previous = REREVIEW_OF.findall(message)
    if previous and verdict in ("PASS", "FINDINGS"):
        extra = {"mode": "re-review", "previous": previous[-1]}
        refusal = rereview_refusal(root, load_state(root), previous[-1], commit) if commit else \
            "the reviewed commit is not named"
        if refusal:
            extra.update(reason=refusal, stated=verdict)
            verdict = "REREVIEW-REFUSED"
    append_event(root, "review", verdict=verdict, tree=tree, commit=commit,
                 agent=data.get("agent_type"), session=data.get("session_id"), **extra)


# --------------------------------------------------------------------------
# Stop: the hand-over gate
# --------------------------------------------------------------------------


def waived(state, tree, gate):
    for w in reversed(events(state, "waiver")):
        if w.get("tree") == tree and w.get("gate") in (gate, "all"):
            return w
    return None


def gh_api(root, path, timeout=40, missing_ok=False, fields=None):
    """`gh api <path>` over GitHub's REST API - GraphQL (`gh pr view`, `gh pr checks`) is refused
    from Claude Code cloud sessions (KAN-154/155), REST works there and on the Mac alike.
    `{owner}/{repo}` in `path` are filled in by `gh` from the checkout's remote.
    Returns (data or None, problem or None); a refusal is reported in GitHub's own words.
    `missing_ok`: an HTTP 404 means "there is none" (e.g. an unprotected branch), not a problem.
    `fields`: a dict sent as a POST body (the one write the gates make: the check status, KAN-625)."""
    cmd = ["gh", "api", path]
    if fields:
        cmd += ["-X", "POST"] + [arg for key in sorted(fields) for arg in ("-f", "{}={}".format(key, fields[key]))]
    try:
        proc = subprocess.run(cmd, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "GitHub could not be reached through `gh api` ({}).".format(exc.__class__.__name__)
    if proc.returncode != 0:
        if missing_ok and "(HTTP 404)" in (proc.stderr or ""):
            return None, None
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, "GitHub refused `gh api {}`: {}".format(path.split("?")[0], detail[0] if detail else
                                                            "exit {}".format(proc.returncode))
    try:
        return json.loads(proc.stdout or "null"), None
    except ValueError:
        return None, "Could not read the `gh api {}` response.".format(path.split("?")[0])


def gh_api_pages(root, path, key=None, per_page=100, max_pages=10):
    """Every page of a list endpoint: a plain JSON array, or the array under `key` (check-runs).
    Pages explicitly with `page=`: `gh api --paginate` follows next-page links that use numeric
    `repositories/<id>/...` paths, which the cloud proxy refuses."""
    items = []
    sep = "&" if "?" in path else "?"
    for page in range(1, max_pages + 1):
        data, problem = gh_api(root, "{}{}per_page={}&page={}".format(path, sep, per_page, page))
        if problem:
            return None, problem
        batch = (data or {}).get(key) or [] if key else (data or [])
        items.extend(batch)
        if len(batch) < per_page:
            return items, None
    # Never a silently shortened list: a missing failed run would read as green.
    return None, "`gh api {}` has more than {} entries - the gate does not read further.".format(
        path.split("?")[0], per_page * max_pages)


def pick_pull(pulls):
    """The PR `gh pr view <branch>` would show: the open one, else the most recent."""
    if not pulls:
        return None
    open_ones = [p for p in pulls if p.get("state") == "open"]
    return max(open_ones or pulls, key=lambda p: p.get("number") or 0)


def pr_from_rest(pull, check_runs, statuses, required=()):
    """The REST answers shaped as `ci_problem` reads them (formerly `gh pr view --json`).
    A required check (base-branch protection) that has not reported at all counts as pending -
    as GraphQL's EXPECTED did - so a job that never starts cannot let the gate through."""
    state = "MERGED" if pull.get("merged_at") else (pull.get("state") or "").upper()
    rollup = [{"name": c.get("name"), "status": (c.get("status") or "").upper(),
               "conclusion": (c.get("conclusion") or "").upper()} for c in check_runs]
    rollup += [{"__typename": "StatusContext", "context": st.get("context"), "state": (st.get("state") or "").upper()}
               for st in statuses]
    reported = {c.get("name") for c in check_runs} | {st.get("context") for st in statuses}
    rollup += [{"__typename": "StatusContext", "context": name, "state": "EXPECTED"}
               for name in required if name not in reported]
    return {"number": pull.get("number"), "url": pull.get("html_url"), "state": state,
            "headRefOid": (pull.get("head") or {}).get("sha"), "statusCheckRollup": rollup}


def branch_pull(root, branch):
    """(pull or None, problem or None) for `branch` in this checkout's GitHub repository."""
    pulls, problem = gh_api(root, "repos/{{owner}}/{{repo}}/pulls?state=all&per_page=100&head={{owner}}:{}".format(
        urllib.parse.quote(branch, safe="")))
    if problem:
        return None, problem
    return pick_pull(pulls or []), None


def pr_status(root, branch):
    """Return (problem or None, pr dict or None)."""
    if not shutil.which("gh"):
        return "The GitHub CLI `gh` is not installed, so the PR and CI cannot be verified " \
               "(`brew install gh`, `gh auth login`).", None
    pull, problem = branch_pull(root, branch)
    if problem:
        return problem + " - so the PR and CI cannot be verified.", None
    if pull is None:
        return "No pull request found for `{}`. Open it (`gh api -X POST repos/{{owner}}/{{repo}}/pulls`, or the " \
               "GitHub tools; title: what changed; body: exit criteria met / not met, verification, " \
               "screenshots).".format(branch), None
    sha = (pull.get("head") or {}).get("sha")
    check_runs, problem = gh_api_pages(root, "repos/{{owner}}/{{repo}}/commits/{}/check-runs".format(sha),
                                       key="check_runs")
    if problem:
        return problem + " - so CI cannot be verified.", None
    statuses, problem = gh_api_pages(root, "repos/{{owner}}/{{repo}}/commits/{}/statuses".format(sha))
    if problem:
        return problem + " - so CI cannot be verified.", None
    required, problem = required_checks(root, (pull.get("base") or {}).get("ref") or "main")
    if problem:
        return problem + " - so the required checks cannot be read.", None
    return None, pr_from_rest(pull, check_runs, latest_statuses(statuses), required)


def required_checks(root, base):
    """(names, problem): the status checks `base`'s branch protection requires (KAN-626). An unprotected
    branch (404) requires none. Both of GitHub's lists are read: `contexts` and the newer `checks`."""
    protection, problem = gh_api(root, "repos/{{owner}}/{{repo}}/branches/{}/protection/required_status_checks".format(
        urllib.parse.quote(base, safe="")), missing_ok=True)
    if problem:
        return [], problem
    names = list((protection or {}).get("contexts") or [])
    names += [c.get("context") for c in (protection or {}).get("checks") or [] if c.get("context")]
    return list(dict.fromkeys(names)), None


def override_notes(check_runs, statuses, required):
    """KAN-626: for each required check that was not green on the merged PR's head, the line `close` puts
    on the ticket - «merged by admin override, <check> was <state>». Anto may merge on red (his ruling,
    2026-11-10); the override is visible, never silent. `check_runs` and `statuses` are newest first."""
    runs, latest = {}, {}
    for c in check_runs:
        runs.setdefault(c.get("name"), c)
    for st in statuses:
        latest.setdefault(st.get("context"), st)
    notes = []
    for name in required:
        if name in latest:
            state = (latest[name].get("state") or "unknown").lower()
            green = state == "success"
        elif name in runs:
            state = (runs[name].get("conclusion") or runs[name].get("status") or "unknown").lower()
            green = state in ("success", "neutral", "skipped")
        else:
            state, green = "not reported", False
        if not green:
            notes.append("merged by admin override, {} was {}".format(name, state))
    return notes


def latest_statuses(statuses):
    """`commits/{sha}/statuses` lists every status ever posted, newest first; keep the newest per
    context (what the combined status reports)."""
    seen, latest = set(), []
    for st in statuses:
        if st.get("context") not in seen:
            seen.add(st.get("context"))
            latest.append(st)
    return latest


def ci_state(pr):
    """(pending, failed) check names of a PR, from `pr_from_rest`'s rollup."""
    pending, failed = [], []
    for c in pr.get("statusCheckRollup") or []:
        name = c.get("name") or c.get("context") or "?"
        if c.get("__typename") == "StatusContext":
            state = (c.get("state") or "").upper()
            if state in ("PENDING", "EXPECTED"):
                pending.append(name)
            elif state not in ("SUCCESS",):
                failed.append(name)
        else:
            if (c.get("status") or "").upper() != "COMPLETED":
                pending.append(name)
            elif (c.get("conclusion") or "").upper() not in ("SUCCESS", "NEUTRAL", "SKIPPED"):
                failed.append(name)
    return pending, failed


def ci_problem(pr, head):
    """The hand-over happens when the PR opens (KAN-619; Anto, 2026-11-10): it needs the PR open for
    the local HEAD and no check that has already FAILED. A check still running, or none reported yet
    (a draft PR, which pr-light skips), does not hold it - green CI is a condition of merging, which
    `scripts/dev/gate close` reads (`close_ci_problem`)."""
    if pr.get("state") not in ("OPEN", "MERGED"):
        return "The pull request #{} is {} - open one for this branch.".format(
            pr.get("number"), (pr.get("state") or "unknown").lower())
    if pr.get("headRefOid") and head and pr["headRefOid"] != head:
        return "The PR's head ({}) is not your local HEAD ({}) - push the latest commit.".format(
            pr["headRefOid"][:10], head[:10])
    _pending, failed = ci_state(pr)
    if failed:
        return "CI failed: {}. Fix it, re-run `scripts/dev/check`, and push again.".format(", ".join(failed))
    return None


NIGHTLY_RED = ("failure", "timed_out", "startup_failure")


def close_ci_problem(merged_pr, nightly_run):
    """KAN-619: `close` sets nothing Done on red CI. `merged_pr` is `pr_from_rest` of the merged PR
    (its head's and its merge commit's checks); `nightly_run` the latest completed nightly run of
    test.yml on main (a workflow-run dict, or None). A cancelled run is no verdict. Returns the
    refusal, or None."""
    failed = ci_state(merged_pr)[1]
    if nightly_run and (nightly_run.get("conclusion") or "") in NIGHTLY_RED:
        failed.append("the nightly run of {} on main ({}: {})".format(
            (nightly_run.get("head_sha") or "?")[:10], nightly_run.get("conclusion"), nightly_run.get("html_url")))
    if not failed:
        return None
    return ("CI is red for the merged work: {}. The ticket is not Done: file a Bug ticket on the Kanban board "
            "naming the failing job, and tell Anto.".format("; ".join(failed)))


def handover_pushes(state, session_id):
    """The pushes whose hand-over this session must complete: its own, or - while a
    hand-over is open - the latest push of any session."""
    pushes = [p for p in events(state, "push") if p.get("session") == session_id]
    if not pushes and handover_open(state):
        pushes = events(state, "push")[-1:]
    return pushes


def handover_check(root, state, pushes):
    """Everything the hand-over still misses for the latest of `pushes`: [(gate, text)]."""
    problems = []
    branch = current_branch(root) or "HEAD"
    head, tree = head_commit(root), head_tree(root)
    last_push = pushes[-1]
    fp = fingerprint(root)
    if fp and tree and fp != tree:
        problems.append(("code", "There are changes after the last commit. Commit, re-run "
                                 "`scripts/dev/check`, and push them (or discard them)."))
    rc, upstream = git(root, "rev-parse", "@{upstream}")
    if rc == 0 and upstream and head and upstream != head:
        problems.append(("push", "HEAD {} is not pushed (upstream is {}).".format(head[:10], upstream[:10])))
    if not waived(state, tree, "ci"):
        problem, pr = pr_status(root, branch)
        if problem:
            problems.append(("ci", problem))
        else:
            problem = ci_problem(pr, head)
            if problem:
                problems.append(("ci", problem))
    review = latest([r for r in events(state, "review") if r.get("tree") == tree])
    if not waived(state, tree, "review") and not (review and review.get("verdict") == "PASS"):
        verdict = (review or {}).get("verdict")
        if verdict == "FINDINGS":
            problems.append(("review", "The reviewer's verdict on this exact code is FINDINGS - address the "
                                       "findings, commit, then run the reviewer again."))
        elif verdict == "REREVIEW-REFUSED":
            problems.append(("review", "The re-review's verdict was refused: {}. Run the reviewer again as a "
                                       "full review (range `origin/main..HEAD`).".format(review.get("reason"))))
        elif verdict == "UNCOMMITTED":
            problems.append(("review", "The reviewer ran while there were uncommitted changes and did not name "
                                       "the commit it reviewed. Commit, then run the reviewer again; its reply "
                                       "ends with `REVIEWED: <sha>` and `VERDICT: PASS`."))
        elif verdict:
            problems.append(("review", "The reviewer's reply for this code had no VERDICT line - run it again."))
        else:
            problems.append(("review", "No reviewer PASS for this exact code. Delegate the final diff to the "
                                       "`reviewer` agent (it ends with `REVIEWED: <sha>` and `VERDICT: PASS`)."))
    shot = latest([e for e in events(state, "screenshot") if e.get("tree") == tree])
    novisual = latest([e for e in events(state, "novisual") if e.get("tree") == tree])
    if not (shot or novisual or waived(state, tree, "screenshot")):
        problems.append(("screenshot", "No screenshot of this exact code. Show the changed screen in the desktop "
                                       "preview - for work without a screen, the nearest visible artefact (the "
                                       "migrated record in the running app, say). Register any other capture "
                                       "with `scripts/dev/gate evidence screenshot <file under .claude/evidence/>`; "
                                       "only if nothing at all can be shown: `scripts/dev/gate no-visual "
                                       "\"<why>\"`, and say so in your reply."))
    notion = [n for n in events(state, "notion") if n.get("at", "") >= last_push.get("at", "")]
    if not (notion or waived(state, tree, "notion")):
        ticket = (state.get("ticket") or {}).get("id")
        if ticket:
            problems.append(("notion", "The Notion ticket {} was not updated after the push. Set In Review and "
                                       "write what shipped: PR link, each exit criterion met / NOT met, "
                                       "verification, screenshot.".format(ticket)))
        else:
            problems.append(("notion", "No active ticket, so no Notion update can be recorded. Register it with "
                                       "`scripts/dev/gate start KAN-<n> <ticket url>`, then set it In Review and "
                                       "write what shipped. A push without a ticket (a spike Anto asked for) is "
                                       "waived instead: `scripts/dev/gate waive notion \"<why>\"`."))
    return problems


def handover_problems(root, state, session_id):
    pushes = handover_pushes(state, session_id)
    if not pushes:
        return None  # no hand-over is due in this session
    return handover_check(root, state, pushes)


def cmd_stop(data):
    root = root_from(data)
    if not root:
        return
    carry_merge_evidence(root)  # KAN-683: a merge the post-tool hook did not see (made outside Bash)
    state = load_state(root)
    session = data.get("session_id")
    ensure_check_status(root, state, handover_pushes(state, session))
    problems = handover_problems(root, state, session)
    if problems == []:
        tree = head_tree(root)
        done = latest(events(state, "handover-complete"))
        if handover_open(state) or not done or done.get("tree") != tree:
            append_event(root, "handover-complete", tree=tree, session=session)
        return
    message = data.get("last_assistant_message") or ""
    if problems and data.get("stop_hook_active") and DECISION_NEEDED.search(message):
        # A stop after the gate already listed what is missing, asking Anto to decide:
        # let it through and keep the hand-over open for the next session.
        append_event(root, "handover-open", items=[g for g, _ in problems], session=session)
        return
    if problems:
        reason = ("Hand-over gate: code was pushed, so the lifecycle must be complete before you stop. "
                  "Missing:\n" +
                  "\n".join("{}. {}".format(i + 1, p) for i, (_gate, p) in enumerate(problems)) +
                  "\nIf one of these truly cannot be met (Notion is down, say), run "
                  "`scripts/dev/gate waive <gate> \"<reason>\"` - Anto approves it - and state the reason in "
                  "your reply. If you need Anto's decision before you can go on, end your reply with a line "
                  "that starts `DECISION NEEDED:` followed by the question, and stop again. Gates: " +
                  ", ".join(sorted({g for g, _ in problems})))
        emit({"decision": "block", "reason": reason})
        return
    if not data.get("stop_hook_active") and COMPLETION_CLAIM.search(message):
        rc, dirty = git(root, "diff", "--name-only", "HEAD")
        if rc == 0 and dirty:
            files = dirty.splitlines()[:10]
            emit({"decision": "block", "reason": (
                "Your reply reads like a hand-over, but these tracked files have uncommitted changes: "
                + ", ".join(files) + ". If the work is finished, complete the lifecycle (commit, "
                "`scripts/dev/check`, reviewer, push, PR, Notion). If you are pausing for Anto's input, "
                "say so plainly and stop again.")})


# --------------------------------------------------------------------------
# CLI: scripts/dev/gate
# --------------------------------------------------------------------------

GATES = ("ci", "review", "screenshot", "notion", "all")


def pr_merged(root, branch):
    """The branch's PR is merged - read over REST (`merged_at`), which works in cloud sessions."""
    if not (branch and shutil.which("gh")):
        return False
    pull, problem = branch_pull(root, branch)
    return problem is None and pull is not None and bool(pull.get("merged_at"))


def merged_ci(root, branch):
    """(refusal or None, note) for `close` (KAN-619): the merged PR's checks - on its head and on the
    merge commit - and the latest completed nightly run on main. A check that cannot be read is a
    refusal too: Done needs the answer."""
    pull, problem = branch_pull(root, branch)
    if problem or pull is None:
        return (problem or "No pull request for `{}`.".format(branch)) + " - CI cannot be confirmed.", ""
    runs, statuses, head_checks = [], [], None
    head_sha = (pull.get("head") or {}).get("sha")
    for sha in dict.fromkeys(filter(None, [head_sha, pull.get("merge_commit_sha")])):
        found_runs, problem = gh_api_pages(root, "repos/{{owner}}/{{repo}}/commits/{}/check-runs".format(sha),
                                           key="check_runs")
        if problem:
            return problem + " - CI cannot be confirmed.", ""
        runs += found_runs
        found, problem = gh_api_pages(root, "repos/{{owner}}/{{repo}}/commits/{}/statuses".format(sha))
        if problem:
            return problem + " - CI cannot be confirmed.", ""
        statuses += latest_statuses(found)
        if sha == head_sha:
            head_checks = (found_runs, found)
    nightly, problem = gh_api(root, "repos/{owner}/{repo}/actions/workflows/test.yml/runs"
                                    "?branch=main&status=completed&per_page=1")
    if problem:
        return problem + " - the nightly run cannot be read.", ""
    latest = ((nightly or {}).get("workflow_runs") or [None])[0]
    merged = pr_from_rest(pull, runs, latest_statuses(statuses))
    note = close_ci_note(merged, latest, pull.get("merged_at"))
    # KAN-626: what the required checks said on the head that merged - an admin override is put on the ticket
    required, problem = required_checks(root, (pull.get("base") or {}).get("ref") or "main")
    if problem:
        note = (note + " " if note else "") + problem + " - whether the merge overrode a required check is unknown."
    elif head_checks:
        overrides = override_notes(head_checks[0], head_checks[1], required)
        if overrides:
            note = (note + " " if note else "") + "On the ticket: «{}».".format("»; «".join(overrides))
    return close_ci_problem(merged, latest), note


def close_ci_note(merged_pr, nightly_run, merged_at):
    """What `close` prints beside its verdict: checks still running, a cancelled nightly, and a
    nightly run that started before the merge - it then says nothing about the merged code yet."""
    notes = []
    pending = ci_state(merged_pr)[0]
    if pending:
        notes.append("Still running on the merged work: {}.".format(", ".join(pending)))
    if nightly_run is None:
        notes.append("No nightly run on main has completed yet - the merged code has had no full run.")
    else:
        if (nightly_run.get("conclusion") or "") == "cancelled":
            notes.append("The latest nightly run was cancelled - no verdict.")
        if merged_at and (nightly_run.get("created_at") or "") < merged_at:
            notes.append("The latest nightly run ({}) started before the merge ({}): it does not cover the merged "
                         "code yet - say so in the Done callout.".format(nightly_run.get("created_at"), merged_at))
    return " ".join(notes)


def cli(argv):
    root = toplevel(os.getcwd())
    if not root:
        print("Not inside a git checkout.", file=sys.stderr)
        return 2
    cmd = argv[0] if argv else "status"
    rest = argv[1:]
    state = load_state(root)
    if cmd == "start":
        if len(rest) < 2 or not re.match(r"^KAN-\d+$", rest[0]):
            print("usage: scripts/dev/gate start KAN-<n> <notion url or page id>", file=sys.stderr)
            return 2
        page = notion_id(rest[1])
        if not page:
            print("Could not find a Notion page id in: " + rest[1], file=sys.stderr)
            return 2

        def start(st):
            previous = (st.get("ticket") or {}).get("id")
            st["ticket"] = {"id": rest[0], "page_id": page, "url": rest[1], "started": now()}
            if previous and previous != rest[0]:
                st["events"] = [e for e in st.get("events", []) if e.get("kind") != "notion"]
        update_state(root, start)
        print("Active ticket: {} (Notion page {}). Writes to this page count as its hand-over evidence."
              .format(rest[0], page))
        return 0
    if cmd == "close":
        abandon = "--abandon" in rest
        branch = current_branch(root)
        pending = state.get("ticket") or handover_open(state)
        if pending and not abandon and not pr_merged(root, branch):
            print("The pull request for `{}` is not merged (or GitHub cannot be asked), so the ticket stays open. "
                  "`close` is for after Anto merged it; to drop the ticket without a merge, Anto runs "
                  "`scripts/dev/gate close --abandon`.".format(branch), file=sys.stderr)
            return 2
        if pending and not abandon:
            refusal, note = merged_ci(root, branch)
            if note:
                print(note)
            if refusal:
                print(refusal, file=sys.stderr)
                return 3

        def close(st):
            ticket = st.pop("ticket", None)
            if handover_open(st):
                st.setdefault("events", []).append({"kind": "handover-complete", "at": now(),
                                                    "tree": head_tree(root),
                                                    "reason": "ticket abandoned" if abandon else "ticket closed"})
            return ticket
        ticket = update_state(root, close)
        print("Closed {}.".format((ticket or {}).get("id", "no active ticket")))
        return 0
    if cmd == "waive":
        if len(rest) < 2 or rest[0] not in GATES or not " ".join(rest[1:]).strip():
            print('usage: scripts/dev/gate waive <{}> "<reason>"'.format("|".join(GATES)), file=sys.stderr)
            return 2
        tree = fingerprint(root)
        append_event(root, "waiver", gate=rest[0], reason=" ".join(rest[1:]), tree=tree)
        print("Waived `{}` for tree {}: {}. State this in your reply.".format(
            rest[0], (tree or "?")[:12], " ".join(rest[1:])))
        return 0
    if cmd == "no-visual":
        if not rest or not " ".join(rest).strip():
            print('usage: scripts/dev/gate no-visual "<why this change has no screen>"', file=sys.stderr)
            return 2
        append_event(root, "novisual", reason=" ".join(rest), tree=fingerprint(root))
        print("Recorded: no visible artefact for this code ({}). Say so in your reply.".format(" ".join(rest)))
        return 0
    if cmd == "evidence":
        if len(rest) != 2 or rest[0] != "screenshot" or not os.path.isfile(rest[1]):
            print("usage: scripts/dev/gate evidence screenshot <existing image file>", file=sys.stderr)
            return 2
        path = os.path.abspath(rest[1])
        inside = path.startswith(os.path.normpath(root) + os.sep)
        if inside and git(root, "check-ignore", "-q", path)[0] != 0:
            print("{} is inside the checkout and not ignored, so it would change the code tree it is evidence "
                  "for. Save captures under .claude/evidence/ and register that file.".format(rest[1]),
                  file=sys.stderr)
            return 2
        append_event(root, "screenshot", tool="file:" + rest[1], tree=fingerprint(root))
        print("Recorded screenshot " + rest[1])
        return 0
    if cmd == "fingerprint":
        print(fingerprint(root) or "")
        return 0
    if cmd == "stamp-checks":
        if len(rest) < 2 or rest[1] not in ("pass", "fail"):
            print("usage: stamp-checks <tree> pass|fail [lanes...] [not-run=<lanes,>] [counts=<text>]",
                  file=sys.stderr)
            return 2
        # KAN-625: what the commit status says beside the lanes - the lanes left out and the test
        # counts. A lane name never holds `=`.
        extras = dict(a.split("=", 1) for a in rest[2:] if "=" in a)
        record = {"tree": rest[0], "result": rest[1], "lanes": [a for a in rest[2:] if "=" not in a], "at": now()}
        if "not-run" in extras:
            record["not_run"] = [lane for lane in extras["not-run"].split(",") if lane]
        if extras.get("counts"):
            record["counts"] = extras["counts"]
        update_state(root, lambda st: st.__setitem__("checks", record))
        return 0
    if cmd == "review-scope":
        # KAN-620: may the next review be a re-review of <previous sha>..HEAD? Read-only.
        if len(rest) != 1:
            print("usage: scripts/dev/gate review-scope <previously reviewed sha>", file=sys.stderr)
            return 2
        refusal = rereview_refusal(root, state, rest[0], head_commit(root) or "HEAD")
        if refusal:
            print("FULL REVIEW: {}.".format(refusal))
            return 1
        base, merge = rereview_base(root, rest[0], head_commit(root) or "HEAD")
        if merge:
            # KAN-683: a merge of main - the re-review covers its resolution only
            print("RE-REVIEW allowed for the resolution of the merge {} of main: the previous commit {} is its first "
                  "parent and the resolution is small and touches no preserved file, migration or public.py. Brief "
                  "the range as the resolution - `git diff {} {}` (git's own merge result, conflict markers "
                  "included, against the merge) - not {}..HEAD, which holds every line main gained."
                  .format(merge["merge"][:10], rest[0][:10], base, merge["merge"], rest[0][:10]))
            return 0
        print("RE-REVIEW allowed for {}..HEAD: the previous commit is a reviewed ancestor and the range is small "
              "and touches no preserved file, migration or public.py.".format(rest[0][:10]))
        return 0
    if cmd == "status":
        tree, fp = head_tree(root), fingerprint(root)
        checks = state.get("checks") or {}
        ticket = state.get("ticket")
        print("Checkout : {}  branch {}".format(root, current_branch(root)))
        print("HEAD tree: {}   working copy: {}".format((tree or "?")[:12],
                                                         "= HEAD" if fp == tree else (fp or "?")[:12] + " (uncommitted changes)"))
        print("Ticket   : {}".format("{} ({})".format(ticket.get("id"), ticket.get("url")) if ticket else
                                     "none - `scripts/dev/gate start KAN-<n> <url>` registers one"))
        print("Check    : {} for tree {} ({})".format(checks.get("result", "never run"),
                                                      (checks.get("tree") or "-")[:12], checks.get("at", "-")))
        review = latest([r for r in events(state, "review") if r.get("tree") == fp])
        print("Review   : {}".format(
            "{} for this code{}".format(review.get("verdict"), " ({}: {})".format(review.get("mode"), review["reason"])
                                        if review.get("reason") else
                                        " (carried over the merge from {})".format(review["carried_from"][:10])
                                        if review.get("carried_from") else "") if review else "none for this code"))
        posted = latest(events(state, "check-status"))
        if posted:
            print("PR status: {} {} on {}: {}".format(CHECK_STATUS_CONTEXT, (posted.get("result") or "?").upper(),
                                                      (posted.get("commit") or "?")[:10], posted.get("detail")))
        shots = [e for e in events(state, "screenshot") if e.get("tree") == fp]
        novisual = latest([e for e in events(state, "novisual") if e.get("tree") == fp])
        print("Screens  : {} for this code{}".format(len(shots), " (no-visual noted)" if novisual else ""))
        push = push_problems(root, [], fetch=False)  # read-only: judged by the last fetched origin/main
        print("Push gate: " + ("would pass" if not push else "would BLOCK:\n  - " + "\n  - ".join(push)))
        pushes = events(state, "push")
        if pushes:
            problems = handover_check(root, state, pushes[-1:])
            opened = handover_open(state)
            print("Hand-over: {}{}".format(
                "OPEN since {} - ".format(opened.get("at")) if opened else "",
                "complete for the last push" if not problems else
                "would BLOCK:\n  - " + "\n  - ".join("{}: {}".format(g, p) for g, p in problems)))
        else:
            print("Hand-over: nothing pushed from this checkout yet")
        return 0
    if cmd == "evidence-verdict":
        # Read-only: what .github/workflows/check-evidence.yml posts for a commit (KAN-647).
        if len(rest) != 1:
            print("usage: nexotec_hooks.py cli evidence-verdict <commit>", file=sys.stderr)
            return 2
        print(json.dumps(evidence_verdict(root, rest[0]) or {}))
        return 0
    if cmd == "selftest":
        return selftest()
    print("unknown command: " + cmd, file=sys.stderr)
    return 2


def selftest():
    """Cheap assertions run by scripts/dev/check whenever .claude/ changes."""
    def words(command):
        return [w for w, _r in shell_commands(command)]

    # --- reading shell commands
    assert words("cd x && git push -u origin HEAD") == [["cd", "x"], ["git", "push", "-u", "origin", "HEAD"]]
    assert words("timeout 120 git push origin HEAD") == [["git", "push", "origin", "HEAD"]]
    assert words("nohup nice -n 5 env FOO=1 git push") == [["git", "push"]]
    assert words("(git push -u origin HEAD)") == [["git", "push", "-u", "origin", "HEAD"]]
    assert ["git", "push"] in words("echo $(git push)")
    assert words("bash -c 'git push origin HEAD'") == [["git", "push", "origin", "HEAD"]]
    assert words("if true; then git push; fi") == [["true"], ["git", "push"], []]
    msg = "git commit -m \"$(cat <<'EOF'\nFix x\n\ngit push origin main\nEOF\n)\""
    assert words(msg) == [["cat"], ["git", "commit", "-m", "$(...)"]], words(msg)
    assert words("git log --grep 'a;b|c'") == [["git", "log", "--grep", "a;b|c"]]
    assert words("git push 2>&1 | tail -5") == [["git", "push"], ["tail", "-5"]]
    assert shell_commands("echo x > out.txt 2>/dev/null") == [(["echo", "x"], [(">", "out.txt"), (">", "/dev/null")])]
    assert git_verb(["git", "-C", "/tmp/x", "push", "origin"]) == ("push", ["origin"])
    assert [w for _t, _r, w in located_commands("cd sub && git -C ../x push", "/r")] == ["/r/x"]
    # --- push
    assert push_destinations(["-u", "origin", "HEAD"], "main") == ["main"]
    assert push_destinations(["origin", "HEAD:refs/heads/main"], "kan-1-x") == ["main"]
    assert push_destinations(["origin", "+kan-1-x"], "kan-1-x") == ["kan-1-x"]
    assert push_destinations([], "kan-1-x") == ["kan-1-x"]
    assert push_destinations(["-o", "ci.skip", "origin", "kan-1-x"], "kan-1-x") == ["kan-1-x"]
    assert push_pairs(["origin", "kan-9"], "kan-1") == [("kan-9", "kan-9")]
    assert "f" in option_letters(["-uf", "origin"], takes_value="o")
    assert "f" not in option_letters(["-o", "-f", "origin"], takes_value="o")
    assert "n" in option_letters(["-anm", "msg"], takes_value="mFcCt", takes_attached="Su")
    assert "n" not in option_letters(["-m", "-n"], takes_value="mFcCt", takes_attached="Su")
    assert "n" not in option_letters(["-mn"], takes_value="mFcCt", takes_attached="Su")
    assert push_mode(["origin", "--delete", "kan-1-x"]) == "delete" and push_mode(["-n", "origin"]) == "dry-run"
    assert push_mode(["-u", "origin", "HEAD"]) == "push"
    # --- writes
    assert write_targets(["sed", "-i", "", "s/a/b/", "f.py"], [], "/r") == ["/r/s/a/b", "/r/f.py"]
    assert write_targets(["cp", "a.py", "dir"], [], "/r") == ["/r/dir", "/r/dir/a.py"]
    assert write_targets(["git", "mv", "a", "b"], [], "/r") == ["/r/a", "/r/b"]
    assert write_targets(["cat", "x"], [(">", "y"), (">", "/dev/null")], "/r") == ["/r/y"]
    assert write_targets(["grep", "-rn", "x", "."], [], "/r") == []
    assert classify_write(None, "/r/.git/nexotec-gates/state.json")[0] == "deny"
    # a preserved-file or gate-file "ask" reaches Anto only in an attended mode; elsewhere it is denied (KAN-255)
    for mode in ("default", "acceptEdits", "plan"):
        assert settle_ask({"permission_mode": mode}, "ask", "x") == ("ask", "x")
    for mode in ("auto", "bypassPermissions", "dontAsk", "someFutureMode", None):
        decision, why = settle_ask({"permission_mode": mode} if mode else {}, "ask", "x")
        assert decision == "deny" and why.startswith("x") and "ask Anto in chat" in why, (mode, why)
    assert settle_ask({"permission_mode": "auto"}, "deny", "y") == ("deny", "y")
    assert settle_ask({"permission_mode": "auto"}, None, "") == (None, "")
    # --- gate CLI and hand-over
    assert gate_cli_command(["python3", ".claude/hooks/nexotec_hooks.py", "cli", "stamp-checks"]) == "stamp-checks"
    assert gate_cli_command(["scripts/dev/gate", "waive", "ci", "x"]) == "waive"
    assert gate_cli_command(["scripts/dev/check", "--full"]) is None
    assert handover_open({"events": [{"kind": "handover-open"}, {"kind": "handover-complete"}]}) is None
    assert handover_open({"events": [{"kind": "handover-complete"}, {"kind": "handover-open", "at": "t"}]})
    # --- reviewer
    assert reviewer_problem(["ruff", "check", "--fix", "app"], [], "/r", "/r")
    assert reviewer_problem(["ruff", "check", "app"], [], "/r", "/r") is None
    assert reviewer_problem(["git", "diff", "origin/main..HEAD"], [], "/r", "/r") is None
    assert reviewer_problem(["git", "commit", "-m", "x"], [], "/r", "/r")
    assert reviewer_problem(["npx", "vitest", "run", "-u"], [], "/r", "/r")
    # the verdict in the last message, or in a hand-back tool call when the message is a "Done"
    report = "Findings: none.\nREVIEWED: 0123abc\nVERDICT: PASS"
    assert subagent_report({"last_assistant_message": report}) == report
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        fh.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "SubagentHandback", "input": {"message": report}}]}}) + "\n")
        fh.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Done."}]}}) + "\n")
    try:
        got = subagent_report({"last_assistant_message": "Done.", "agent_transcript_path": fh.name})
        assert REVIEW_VERDICT.findall(got) == ["PASS"] and REVIEWED_COMMIT.findall(got) == ["0123abc"], got
        assert subagent_report({"last_assistant_message": "Done.", "agent_transcript_path": fh.name + ".gone"}) \
            == "Done."
    finally:
        os.unlink(fh.name)
    # --- GitHub over REST (KAN-155): recorded response shapes, no network
    head_sha = "6266cfb0bfc512858711c07847fe192c8e8fdb22"
    pull_open = {"number": 7, "state": "open", "merged_at": None, "html_url": "https://github.com/o/r/pull/7",
                 "head": {"sha": head_sha}}
    pull_old = {"number": 3, "state": "closed", "merged_at": None, "head": {"sha": "0" * 40}}
    pull_merged = {"number": 9, "state": "closed", "merged_at": "2026-10-06T05:51:51Z", "head": {"sha": head_sha}}
    assert pick_pull([pull_old, pull_open]) is pull_open and pick_pull([pull_old, pull_merged]) is pull_merged
    assert pick_pull([pull_merged, pull_open]) is pull_open  # the open PR wins over a newer closed one
    assert pick_pull([]) is None
    green = [{"name": "pytest", "status": "completed", "conclusion": "success"},
             {"name": "docs", "status": "completed", "conclusion": "skipped"}]
    pr = pr_from_rest(pull_open, green, [])
    assert pr["headRefOid"] == head_sha and pr["state"] == "OPEN" and pr["number"] == 7
    assert ci_problem(pr, head_sha) is None
    assert "not your local HEAD" in ci_problem(pr, "f" * 40)
    # KAN-619: the hand-over waits for nothing GitHub has not decided yet - only a failure blocks
    assert ci_problem(pr_from_rest(pull_open, [], []), head_sha) is None  # a draft: nothing reported
    pending = green + [{"name": "mypy", "status": "in_progress", "conclusion": None}]
    assert ci_problem(pr_from_rest(pull_open, pending, []), head_sha) is None
    assert ci_state(pr_from_rest(pull_open, pending, [])) == (["mypy"], [])
    assert "is closed" in ci_problem(pr_from_rest(pull_old, green, []), "0" * 40)
    failed = green + [{"name": "ruff", "status": "completed", "conclusion": "failure"}]
    assert "CI failed: ruff" in ci_problem(pr_from_rest(pull_open, failed, []), head_sha)
    status_pending = [{"context": "deploy/preview", "state": "pending"}]
    assert ci_problem(pr_from_rest(pull_open, green, status_pending), head_sha) is None
    status_error = [{"context": "deploy/preview", "state": "error"}]
    assert "CI failed: deploy/preview" in ci_problem(pr_from_rest(pull_open, green, status_error), head_sha)
    assert pr_from_rest(pull_merged, green, [])["state"] == "MERGED"
    # a required check that never reported counts as pending (GraphQL's EXPECTED) - it no longer holds the gate
    assert ci_state(pr_from_rest(pull_open, green, [], ["pytest", "mypy"]))[0] == ["mypy"]
    assert ci_problem(pr_from_rest(pull_open, green, [], ["pytest", "mypy"]), head_sha) is None
    # close (KAN-619): green merged work and a green nightly close; a red check or a red nightly refuses Done
    merged_green = pr_from_rest(pull_merged, green, [])
    nightly_green = {"conclusion": "success", "head_sha": "a" * 40, "html_url": "https://x/runs/1"}
    nightly_red = {"conclusion": "failure", "head_sha": "a" * 40, "html_url": "https://x/runs/2"}
    assert close_ci_problem(merged_green, nightly_green) is None
    assert close_ci_problem(merged_green, None) is None
    red = close_ci_problem(pr_from_rest(pull_merged, failed, []), nightly_green)
    assert red and "ruff" in red and "not Done" in red and "Bug ticket" in red
    assert "nightly run of aaaaaaaaaa" in close_ci_problem(merged_green, nightly_red)
    assert close_ci_problem(pr_from_rest(pull_merged, pending, []), nightly_green) is None  # still running: not red
    assert close_ci_problem(merged_green, dict(nightly_green, conclusion="cancelled")) is None  # no verdict
    assert "cancelled" in close_ci_note(merged_green, dict(nightly_green, conclusion="cancelled"), None)
    early = dict(nightly_green, created_at="2026-10-06T02:17:00Z")
    assert "started before the merge" in close_ci_note(merged_green, early, "2026-10-06T05:51:51Z")
    assert close_ci_note(merged_green, dict(early, created_at="2026-10-07T02:17:00Z"), "2026-10-06T05:51:51Z") == ""
    # KAN-626: a merge with a required check not green is put on the ticket as an admin override
    required = ["nexotec/check", "pr-light"]
    light_ok = [{"name": "pr-light", "status": "completed", "conclusion": "success"}]
    check_ok = [{"context": "nexotec/check", "state": "success"}]
    assert override_notes(light_ok, check_ok, required) == []
    assert override_notes(light_ok, check_ok, []) == []  # unprotected: nothing is required
    assert override_notes([{"name": "pr-light", "status": "completed", "conclusion": "failure"}], check_ok, required) \
        == ["merged by admin override, pr-light was failure"]
    assert override_notes([{"name": "pr-light", "status": "in_progress", "conclusion": None}], [], required) == \
        ["merged by admin override, nexotec/check was not reported", "merged by admin override, pr-light was in_progress"]
    # newest first: a later success over an earlier error is green, a later error over a success is not
    assert override_notes(light_ok, [check_ok[0], {"context": "nexotec/check", "state": "error"}], required) == []
    assert override_notes(light_ok, [{"context": "nexotec/check", "state": "error"}, check_ok[0]], required) == \
        ["merged by admin override, nexotec/check was error"]
    # --- KAN-618: generated files are never printed whole; grep and a bounded range still work
    gen = "frontend/apps/dms/src/api/schema.d.ts"

    def read_problem(command, where="/r"):
        return [whole_read_problem(w, r, where, "/r") for w, r in shell_commands(command)]
    for refused in ("cat " + gen, "less " + gen, "head -n 500 " + gen, "head -500 " + gen, "head --lines=300 " + gen,
                    "tail -n +1 " + gen, "sed -n '1,$p' " + gen, "sed -n p " + gen, "sed 's/a/b/' " + gen,
                    "sed -n '1~1p' " + gen, "sed -n '$!p' " + gen,
                    "sed -n '1,400p' " + gen, "cat < " + gen, "head -c 99999 " + gen, "cat frontend/package-lock.json",
                    "cat ../api/schema.d.ts"):
        where = "/r/frontend/apps/dms/src/x" if refused.startswith("cat ../") else "/r"
        found = [p for p in read_problem(refused, where) if p]
        assert found and "scripts/dev/api-type" in found[0] and "grep -n" in found[0], refused
    for allowed in ("grep -n CustomerRead " + gen, "head -n 50 " + gen, "head " + gen, "sed -n '6600,6700p' " + gen,
                    "sed -n '/CustomerRead: {/p' " + gen, "tail -n 20 " + gen, "wc -l " + gen, "cat README.md",
                    "grep -c x frontend/package-lock.json", "sed -i 's/a/b/' other.txt"):
        assert not any(read_problem(allowed)), allowed
    # the Read tool: a whole read is refused, a bounded one (offset + limit <= 200) is not; other files untouched
    assert "scripts/dev/api-type" in whole_read_tool_problem("/r", {"file_path": "/r/" + gen})
    assert whole_read_tool_problem("/r", {"file_path": "/r/" + gen, "limit": 500})
    assert whole_read_tool_problem("/r", {"file_path": "/r/frontend/package-lock.json"})
    assert whole_read_tool_problem("/r", {"file_path": "/r/" + gen, "offset": 6600, "limit": 120}) is None
    assert whole_read_tool_problem("/r", {"file_path": "/r/frontend/apps/dms/src/api/types.ts"}) is None
    newest_first = [{"context": "deploy", "state": "success"}, {"context": "deploy", "state": "pending"}]
    assert latest_statuses(newest_first) == newest_first[:1]
    real_run = subprocess.run
    try:
        subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", (
            "HTTP 403: GitHub GraphQL is not available from Claude Code sessions\n"))
        data, problem = gh_api("/", "repos/{owner}/{repo}/pulls?head=x")
        assert data is None and problem == ("GitHub refused `gh api repos/{owner}/{repo}/pulls`: HTTP 403: GitHub "
                                            "GraphQL is not available from Claude Code sessions"), problem
        subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a[0], 0, json.dumps([pull_merged]), "")
        assert branch_pull("/", "kan-1-x") == (pull_merged, None)
        pages = iter([json.dumps({"check_runs": green * 50}), json.dumps({"check_runs": green[:1]})])
        subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a[0], 0, next(pages), "")
        runs, problem = gh_api_pages("/", "repos/{owner}/{repo}/commits/x/check-runs", key="check_runs")
        assert problem is None and len(runs) == 101
        subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a[0], 0, json.dumps({"check_runs": green * 50}), "")
        runs, problem = gh_api_pages("/", "repos/{owner}/{repo}/commits/x/check-runs", key="check_runs")
        assert runs is None and "more than 1000" in problem  # never a silently shortened list
        subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "gh: Branch not found (HTTP 404)\n")
        assert gh_api("/", "repos/{owner}/{repo}/branches/x/protection", missing_ok=True) == (None, None)
        assert gh_api("/", "repos/{owner}/{repo}/branches/x/protection")[1].endswith("(HTTP 404)")
        assert required_checks("/", "main") == ([], None)  # KAN-626: an unprotected branch requires nothing
        subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a[0], 0, json.dumps(
            {"strict": True, "contexts": ["nexotec/check", "pr-light"],
             "checks": [{"context": "nexotec/check", "app_id": None}, {"context": "pr-light", "app_id": 15368}]}), "")
        assert required_checks("/", "main") == (["nexotec/check", "pr-light"], None)
    finally:
        subprocess.run = real_run
    # --- KAN-625: the check result as a commit status, only ever for the tree that passed
    tree_a, tree_b = "a" * 40, "b" * 40
    passed = {"tree": tree_a, "result": "pass", "lanes": ["frontend"], "not_run": ["backend", "migrations"],
              "counts": "dms vitest 1890 passed", "at": "t"}
    status = check_status_payload(passed, tree_a)
    assert status == {"state": "success", "context": "nexotec/check",
                      "description": "frontend only · tree aaaaaaaaaaaa · dms vitest 1890 passed"}, status
    assert check_status_payload(passed, tree_b) is None  # another tree: nothing to post
    assert check_status_payload(dict(passed, result="fail"), tree_a) is None
    assert check_status_payload({}, tree_a) is None and check_status_payload(None, tree_a) is None
    assert check_status_payload(passed, None) is None and check_status_payload(passed, "") is None
    full = check_status_payload(dict(passed, lanes=["config", "backend"], not_run=[], counts="pytest 4213 passed"),
                                tree_a)
    assert full["description"] == "all lanes · tree aaaaaaaaaaaa · pytest 4213 passed", full
    legacy = {k: v for k, v in passed.items() if k not in ("not_run", "counts")}
    assert check_status_payload(legacy, tree_a)["description"] == \
        "frontend · tree aaaaaaaaaaaa · no test counts recorded"  # never "all lanes" on a record that cannot say
    assert check_status_payload(dict(passed, lanes=[]), tree_a)["description"].startswith("no lane covers")
    assert len(check_status_payload(dict(passed, counts="x" * 300), tree_a)["description"]) == CHECK_STATUS_MAX
    # the status is not typed: a write naming the context is refused, a read or a search is not
    for typed in ("gh api -X POST repos/o/r/statuses/abc -f state=success -f context=nexotec/check",
                  "gh api repos/o/r/statuses/abc --method POST --field context=nexotec/check",
                  "curl -d '{\"context\": \"nexotec/check\"}' https://api.github.com/repos/o/r/statuses/abc",
                  # the forms the first guard missed: an attached field, a file, a variable, a check run
                  "gh api repos/o/r/statuses/abc -fcontext=nexotec/check -fstate=success",
                  "gh api repos/{owner}/{repo}/statuses/abc --input status.json",
                  "curl --data @status.json https://api.github.com/repos/o/r/statuses/abc",
                  "gh api /repos/o/r/statuses/$SHA -f context=$C -f state=success",
                  "gh api repos/o/r/check-runs -f name=nexotec/check -f head_sha=abc",
                  "python3 -c 'post(\"nexotec/check\")'"):
        assert any(types_check_status(w) for w in words(typed)), typed
    for harmless in ("gh api repos/o/r/commits/abc/statuses --jq '.[] | select(.context==\"nexotec/check\")'",
                     "gh api repos/o/r/commits/abc/check-runs", "gh api repos/o/r/commits/abc/status",
                     "grep -rn nexotec/check .claude", "git commit -m 'post nexotec/check from the hook'",
                     "gh api -X POST repos/{owner}/{repo}/pulls -f title=x -f body='nexotec/check was refused'",
                     "gh api -X POST repos/o/r/pulls -f title=x"):
        assert not any(types_check_status(w) for w in words(harmless)), harmless
    # posting end to end in a throwaway repository, `gh` and git's commit lookup stubbed: the POST
    # goes to the pushed commit with the recorded description; a commit of another tree, and a
    # refusal, post nothing more and are recorded in GitHub's own words
    real_run, real_git, real_load, real_append = subprocess.run, globals()["git"], globals()["load_state"], \
        globals()["append_event"]
    calls, recorded = [], []
    try:
        globals()["git"] = lambda root, *a, **k: (0, ("c" * 40) if a[-1].endswith("^{commit}") else
                                                  (tree_a if a[-1].startswith("good") else tree_b))
        globals()["load_state"] = lambda root: {"checks": passed}
        globals()["append_event"] = lambda root, kind, **f: recorded.append(dict(f, kind=kind))
        subprocess.run = lambda cmd, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "{}", "")
        assert post_check_status("/", "good")["result"] == "posted"
        assert calls == [["gh", "api", "repos/{owner}/{repo}/statuses/" + "c" * 40, "-X", "POST",
                          "-f", "context=nexotec/check",
                          "-f", "description=frontend only · tree aaaaaaaaaaaa · dms vitest 1890 passed",
                          "-f", "state=success"]], calls
        assert post_check_status("/", "other")["result"] == "no-evidence" and len(calls) == 1  # no POST at all
        refusal = "gh: Write access to this GitHub API path is not permitted through this proxy. (HTTP 403)\n"
        subprocess.run = lambda cmd, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 1, "", refusal)
        refused = post_check_status("/", "good")
        assert refused["result"] == "refused" and refused["detail"].endswith("through this proxy. (HTTP 403)")
        assert "NOT posted" in check_status_note(refused) and check_status_note(recorded[0]) == ""
        assert [e["result"] for e in recorded] == ["posted", "no-evidence", "refused"]
        # the hand-over retries only what may still pass: not a posted one, not an HTTP refusal
        before = len(calls)
        ensure_check_status("/", {"events": [dict(recorded[2], commit="c" * 40)]}, [{"head": "c" * 40}])
        ensure_check_status("/", {"events": [dict(recorded[0], commit="c" * 40)]}, [{"head": "c" * 40}])
        assert len(calls) == before
        ensure_check_status("/", {"events": []}, [{"head": "good"}])
        assert len(calls) == before + 1
    finally:
        subprocess.run = real_run
        globals().update(git=real_git, load_state=real_load, append_event=real_append)
    # --- KAN-620: a re-review stands only on a reviewed ancestor and a small, plain range
    assert REREVIEW_OF.findall("RE-REVIEW OF: 0123abc\nREVIEWED: 4567def\nVERDICT: PASS") == ["0123abc"]
    assert REREVIEW_OF.findall("REVIEWED: 4567def\nVERDICT: PASS") == []
    assert REREVIEW_OF.findall("**RE-REVIEW OF:** `0123abc`\nREVIEWED: 4567def") == ["0123abc"]
    # prose is not the marker: a full review that mentions an earlier one stays a full review
    assert REREVIEW_OF.findall("This is not a re-review of 0123abc.\nA full review, no RE-REVIEW OF: 0123abc line.") == []
    assert rereview_full_reason(["3\t1\tapp/sales/services/offer.py", "10\t0\ttests/test_x.py"]) is None
    assert "preserved file" in rereview_full_reason(["1\t0\tapp/core/tenancy.py"])
    assert "preserved file" in rereview_full_reason(["1\t0\tapp/core/outbox_worker.py"])
    assert "migration" in rereview_full_reason(["1\t0\talembic/versions/platform/abc_x.py"])
    assert "public door" in rereview_full_reason(["1\t0\tapp/sales/public.py"])
    assert rereview_full_reason(["1\t0\tfrontend/public.py"]) is None
    assert "binary" in rereview_full_reason(["-\t-\tdocs/x.png"])
    assert "151 lines" in rereview_full_reason(["100\t51\tapp/x.py"])
    assert rereview_full_reason(["100\t50\tapp/x.py"]) is None
    repo = tempfile.mkdtemp(prefix="nexotec-selftest-")
    try:
        ident = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]

        def commit_file(name, text):
            with open(os.path.join(repo, name), "w") as fh:
                fh.write(text)
            git(repo, "add", "-A")
            git(repo, *(ident + ["commit", "-q", "-m", name]))
            return git(repo, "rev-parse", "HEAD")[1]
        git(repo, "init", "-q")
        first = commit_file("a.py", "a\n")
        second = commit_file("b.py", "b\n")
        git(repo, "checkout", "-q", "-b", "side", first)
        side = commit_file("c.py", "c\n")
        git(repo, "checkout", "-q", "-")
        reviewed = {"events": [{"kind": "review", "commit": first, "verdict": "FINDINGS"}]}
        assert rereview_refusal(repo, reviewed, first, second) is None
        assert "no review" in rereview_refusal(repo, {"events": []}, first, second)
        refused_base = {"events": [{"kind": "review", "commit": first, "verdict": "REREVIEW-REFUSED"}]}
        assert "no review" in rereview_refusal(repo, refused_base, first, second)
        assert "not an ancestor" in rereview_refusal(repo, {"events": [{"kind": "review", "commit": side,
                                                                        "verdict": "PASS"}]}, side, second)
        assert "not a commit" in rereview_refusal(repo, reviewed, "f" * 40, second)
        big = commit_file("d.py", "x\n" * 151)
        assert "needs a full review" in rereview_refusal(repo, reviewed, first, big)
        # the SubagentStop handler: a valid re-review PASS is recorded for the new commit; one on
        # an unreviewed or foreign base is recorded as refused, never as PASS
        update_state(repo, lambda st: st.update(reviewed))

        def stop(report):
            cmd_subagent_stop({"cwd": repo, "agent_type": "reviewer", "last_assistant_message": report})
            return events(load_state(repo), "review")[-1]
        ok = stop("RE-REVIEW OF: {}\nREVIEWED: {}\nVERDICT: PASS".format(first, second))
        assert (ok["verdict"], ok["mode"], ok["commit"]) == ("PASS", "re-review", second), ok
        bad = stop("RE-REVIEW OF: {}\nREVIEWED: {}\nVERDICT: PASS".format(side, second))
        assert bad["verdict"] == "REREVIEW-REFUSED" and "not an ancestor" in bad["reason"], bad
        too_big = stop("RE-REVIEW OF: {}\nREVIEWED: {}\nVERDICT: PASS".format(first, big))
        assert too_big["verdict"] == "REREVIEW-REFUSED" and "full review" in too_big["reason"], too_big
        plain = stop("REVIEWED: {}\nVERDICT: PASS".format(big))
        assert (plain["verdict"], plain["mode"]) == ("PASS", "full"), plain
    finally:
        shutil.rmtree(repo, ignore_errors=True)
    assert reviewer_problem(["scripts/dev/gate", "review-scope", "0123abc"], [], "/r", "/r") is None
    assert reviewer_problem(["scripts/dev/gate", "waive", "ci", "x"], [], "/r", "/r")
    # --- Notion
    assert notion_id("https://app.notion.com/p/3cf3e79334dd80f69bb8c2e6a19481ef?pvs=4") == "3cf3e79334dd80f69bb8c2e6a19481ef"
    assert notion_id("3cf3e793-34dd-805c-9017-000b5a24915f") in KANBAN_IDS
    assert notion_id("https://www.notion.so/Margin-drops-3e83e79334dd81c3b6f1d732105a81e6?v=0123456789abcdef0123456789abcdef") == "3e83e79334dd81c3b6f1d732105a81e6"
    assert notion_tool("mcp__claude_ai_Notion__notion-update-page") == "notion-update-page"
    assert notion_decision({"tool_name": "mcp__claude_ai_Notion__notion-fetch"}) is None
    assert notion_decision({"tool_name": "mcp__claude_ai_Notion__notion-update-page"})[0] == "allow"
    assert notion_decision({"tool_name": "mcp__Notion__notion-move-pages"})[0] == "allow"
    assert notion_decision({"tool_name": "mcp__Notion__notion-update-page", "agent_type": "reviewer"})[0] == "deny"
    # KAN-685: replace_content only on the active ticket and on pages this session created
    gap, ticket_page, mine = "3b73e79334dd81cbb181f85d58471d52", "4173e79334dd818a889bf4ddf7a05669", "4" * 32
    notion_state = {"ticket": {"id": "KAN-685", "page_id": ticket_page},
                    "notion_created": [{"page": mine, "session": "s1"}]}

    def notion_write(page, command, **extra):
        return notion_decision(dict({"tool_name": "mcp__claude_ai_Notion__notion-update-page", "session_id": "s1",
                                     "tool_input": {"page_id": page, "command": command}}, **extra), notion_state)
    denied = notion_write("https://app.notion.com/p/" + gap, "replace_content")
    assert denied[0] == "deny" and "update_content" in denied[1] and "insert_content" in denied[1], denied
    assert notion_write(gap, "replace_content", tool_name="mcp__Notion__notion-update-page")[0] == "deny"
    assert notion_write("4173e793-34dd-818a-889b-f4ddf7a05669", "replace_content")[0] == "allow"  # the active ticket
    assert notion_write(mine, "replace_content")[0] == "allow"  # created in this session
    assert notion_write(mine, "replace_content", session_id="s2")[0] == "deny"  # created in another session
    assert notion_write(None, "replace_content")[0] == "deny"
    for command in ("update_content", "insert_content", "update_properties"):
        assert notion_write(gap, command)[0] == "allow", command
    for command in ("replace_content", "update_content"):
        assert notion_write(ticket_page, command, agent_type="reviewer")[0] == "deny", command
    assert notion_decision({"tool_name": "mcp__Notion__notion-create-pages", "agent_type": "reviewer"})[0] == "deny"
    # only pages[].id / url count as created; a parent, ancestor or relation in the answer never does
    parent_echo = json.dumps({"pages": [{"id": "4173e793-34dd-81aa-bbbb-ccccdddd0002",
                                         "properties": {"Parent": "https://app.notion.com/p/" + gap}}],
                              "parent": {"page_id": ticket_page}})
    assert created_page_ids([{"type": "text", "text": parent_echo}]) == {"4173e79334dd81aabbbbccccdddd0002"}
    assert created_page_ids("not json " + gap) == set() and created_page_ids(None) == set()
    assert created_page_ids({"pages": "x"}) == set() and created_page_ids({"pages": [{"url": "nothing"}]}) == set()
    # the post-tool hook remembers what notion-create-pages made - the new page, not its parent
    repo = tempfile.mkdtemp(prefix="nexotec-selftest-notion-")
    try:
        git(repo, "init", "-q")
        new_page = "4173e793-34dd-81aa-bbbb-ccccdddd0001"
        cmd_post_tool({"cwd": repo, "session_id": "s1", "tool_name": "mcp__Notion__notion-create-pages",
                       "tool_input": {"parent": {"data_source_id": "3cf3e793-34dd-805c-9017-000b5a24915f"},
                                      "pages": [{"content": "see https://app.notion.com/p/" + gap}]},
                       "tool_response": [{"type": "text", "text": json.dumps({"pages": [
                           {"id": new_page, "url": "https://app.notion.com/p/" + new_page.replace("-", "")}]})}]})
        created = load_state(repo).get("notion_created")
        assert [c["page"] for c in created] == [new_page.replace("-", "")] and created[0]["session"] == "s1", created
        assert notion_decision({"tool_name": "mcp__Notion__notion-update-page", "session_id": "s1",
                                "tool_input": {"page_id": new_page, "command": "replace_content"}},
                               load_state(repo))[0] == "allow"
    finally:
        shutil.rmtree(repo, ignore_errors=True)
    # --- KAN-686: Render's connector - every recorded tool is allowed (a read) or denied by name, under
    # each server name; nothing in settings.json names a Render tool the record does not; the hook denies
    # a non-read under any name
    assert len(set(RENDER_TOOLS)) == len(RENDER_TOOLS) and set(RENDER_READ_TOOLS) <= set(RENDER_TOOLS)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "settings.json")) as fh:
        settings = json.load(fh)
    permissions = settings.get("permissions") or {}
    allow, deny = set(permissions.get("allow") or []), set(permissions.get("deny") or [])
    for server in RENDER_SERVERS:
        for tool in RENDER_TOOLS:
            name = "{}__{}".format(server, tool)
            listed = (name in allow, name in deny)
            assert listed == ((True, False) if tool in RENDER_READ_TOOLS else (False, True)), (name, listed)
    recorded = {"{}__{}".format(s, t) for s in RENDER_SERVERS for t in RENDER_TOOLS}
    unrecorded = [r for r in allow | deny | set(permissions.get("ask") or []) if r.startswith("mcp__")
                  and "render" in r.split("__")[1].lower() and r not in recorded]
    assert not unrecorded, "settings.json names Render tools the record does not: {}".format(unrecorded)
    assert any(h.get("matcher") == "mcp__.*[Rr]ender.*" and "pre-render" in h["hooks"][0]["command"]
               for h in settings["hooks"]["PreToolUse"]), "the pre-render hook is not registered"
    for server in RENDER_SERVERS + ("mcp__some_future_Render",):
        assert render_decision(server + "__query_render_postgres") is None
        assert render_decision(server + "__list_deploys") is None
        for tool in ("trigger_deploy", "update_environment_variables", "create_postgres", "select_workspace"):
            assert render_decision("{}__{}".format(server, tool))[0] == "deny", (server, tool)
    assert render_decision("mcp__Notion__notion-update-page") is None and render_decision("Bash") is None
    # --- evidence
    assert is_screenshot("mcp__Claude_Preview__preview_screenshot", {})
    assert is_screenshot("mcp__Claude_Browser__computer", {"action": "screenshot"})
    assert is_screenshot("mcp__Claude_Browser__browser_batch",
                         {"actions": [{"name": "navigate", "input": {}},
                                      {"name": "computer", "input": {"action": "screenshot"}}]})
    assert not is_screenshot("mcp__claude-in-chrome__computer", {"action": "screenshot"})
    assert not is_screenshot("mcp__remote-devices__computer_screenshot", {})
    assert not is_screenshot("Bash", {"command": "pytest"})
    assert not is_screenshot("Bash", {"command": "npx playwright screenshot https://github.com/x/y/pull/7 p.png"})
    assert is_screenshot("Bash", {"command": "npx playwright screenshot http://localhost:5173 .claude/evidence/a.png"})
    assert COMPLETION_CLAIM.search("Done - pushed and green") and not COMPLETION_CLAIM.search("Should I proceed?")
    assert REVIEW_VERDICT.findall("...\nVERDICT: PASS") == ["PASS"]
    assert REVIEWED_COMMIT.findall("REVIEWED: 1a2b3c4d5e6f\nVERDICT: PASS") == ["1a2b3c4d5e6f"]
    assert DECISION_NEEDED.search("CI failed twice on a flaky test.\n\nDECISION NEEDED: re-run it or investigate?")
    assert DECISION_NEEDED.search("**DECISION NEEDED:** waive the screenshot?")
    assert not DECISION_NEEDED.search("Pushed and PR opened. Let me know if you'd like any changes.")
    assert not DECISION_NEEDED.search("Done. PR #7 is open.\n\nAnything else?")
    # --- the evidence commit where the status write is refused (KAN-647)
    repo = tempfile.mkdtemp(prefix="nexotec-selftest-evidence-")
    saved_remote = os.environ.get("CLAUDE_CODE_REMOTE")
    try:
        git(repo, "init", "-q", "-b", "kan-1-x")
        git(repo, "config", "user.name", "t")
        git(repo, "config", "user.email", "t@example.invalid")
        git(repo, "config", "commit.gpgsign", "false")
        with open(os.path.join(repo, "a.py"), "w") as fh:
            fh.write("a\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "one")
        base, tree = head_commit(repo), head_tree(repo)
        checks = {"result": "pass", "tree": tree, "lanes": ["backend"], "not_run": ["frontend"],
                  "counts": "pytest 3 passed", "at": "2026-11-13T00:00:00+00:00"}
        update_state(repo, lambda st: st.update({"checks": checks}))
        os.environ.pop("CLAUDE_CODE_REMOTE", None)
        assert add_evidence_commit(repo) is None, "made where the status can be written"
        os.environ["CLAUDE_CODE_REMOTE"] = "true"
        made = add_evidence_commit(repo)
        assert made and head_commit(repo) == made and head_tree(repo) == tree
        assert commit_facts(repo, made)[2] == [tree] and current_branch(repo) == "kan-1-x"
        assert add_evidence_commit(repo) is None and head_commit(repo) == made, "evidence commits stacked"
        ok = evidence_verdict(repo, made)
        assert ok["state"] == "success" and ok["context"] == CHECK_STATUS_CONTEXT, ok
        assert "backend only" in ok["description"] and "evidence commit" in ok["description"], ok
        assert len(ok["description"]) <= CHECK_STATUS_MAX
        assert evidence_verdict(repo, base) is None, "a commit without the marker gets a status"
        # a hand-made empty commit naming another tree, and one with the marker that changes files
        forged = git(repo, "commit-tree", tree, "-p", made, "-m", evidence_message(dict(checks, tree="f" * 40), "f" * 40))[1]
        assert "names" in evidence_verdict(repo, forged)["description"]
        with open(os.path.join(repo, "b.py"), "w") as fh:
            fh.write("b\n")
        git(repo, "add", "-A")
        changed_tree = git(repo, "write-tree")[1]
        changed = git(repo, "commit-tree", changed_tree, "-p", made,
                      "-m", evidence_message(dict(checks, tree=changed_tree), changed_tree))[1]
        bad = evidence_verdict(repo, changed)
        assert bad["state"] == "error" and "changes files" in bad["description"], bad
        # no recorded pass for HEAD's tree: no evidence commit
        git(repo, "reset", "-q", "--hard", base)
        update_state(repo, lambda st: st.update({"checks": dict(checks, tree="0" * 40)}))
        assert add_evidence_commit(repo) is None and head_commit(repo) == base
        os.environ.pop("CLAUDE_CODE_REMOTE", None)
        assert status_write_refused({"events": [{"kind": "check-status", "result": "refused",
                                                 "detail": "GitHub refused `gh api x`: HTTP 403: no (HTTP 403)"}]})
        assert not status_write_refused({"events": [{"kind": "check-status", "result": "posted"}]})
        # only the hook makes one: a typed commit carrying the marker is refused
        marker_file = os.path.join(repo, "msg.txt")
        with open(marker_file, "w") as fh:
            fh.write("x\n\n{} 1\n".format(EVIDENCE_MARKER))
        assert types_evidence_commit(["git", "commit", "--allow-empty", "-m", "x\n\n{} 1".format(EVIDENCE_MARKER)], repo)
        assert types_evidence_commit(["git", "-C", repo, "commit-tree", "t", "-m", EVIDENCE_MARKER + " 1"], repo)
        assert types_evidence_commit(["git", "commit", "-F", "msg.txt"], repo)
        assert types_evidence_commit(["git", "commit", "--file=msg.txt"], repo)
        assert types_evidence_commit(["python3", "-c", "print('{} 1')".format(EVIDENCE_MARKER)], repo)
        assert not types_evidence_commit(["git", "commit", "-m", "fix"], repo)
        assert not types_evidence_commit(["git", "log", "--grep", EVIDENCE_MARKER], repo)

        # the PreToolUse hook end to end: a piped marker is refused; a push that goes out gets the commit first
        def pre_bash(command):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cmd_pre_bash({"cwd": repo, "tool_input": {"command": command}})
            return out.getvalue()
        os.remove(marker_file)
        assert "deny" in pre_bash("printf '{} 1' | git commit-tree {} -F -".format(EVIDENCE_MARKER, tree))
        update_state(repo, lambda st: st.update({"checks": checks}))
        os.environ["CLAUDE_CODE_REMOTE"] = "true"
        assert pre_bash("git push --force origin HEAD") and head_commit(repo) == base, "made for a refused push"
        assert pre_bash("git push -u origin HEAD") == "" and head_commit(repo) != base
        assert parse_evidence(commit_facts(repo, "HEAD")[3])["tree"] == tree
        evidence = head_commit(repo)
        # its message reused, or fed from a file another way, is refused too
        with open(marker_file, "w") as fh:
            fh.write("x\n\n{} 1\n".format(EVIDENCE_MARKER))
        assert types_evidence_commit(["git", "commit", "--amend", "--no-edit"], repo)
        assert types_evidence_commit(["git", "commit", "--allow-empty", "-C", evidence], repo)
        assert types_evidence_commit(["git", "commit", "--reedit-message=HEAD"], repo)
        assert types_evidence_commit(["git", "commit", "-F-"], repo, [("<", "msg.txt")])
        assert types_evidence_commit(["git", "commit-tree", tree], repo, [("<", "msg.txt")])
        assert types_evidence_commit(["git", "commit", "-t", "msg.txt"], repo)
        assert not types_evidence_commit(["git", "commit", "-C", base], repo)
        assert "deny" in pre_bash("git commit --amend --no-edit")
        # a `--tags` push makes none; a push that did not land (no remote here) drops the commit again
        os.remove(marker_file)  # an untracked file would stop the push at the gate
        git(repo, "update-ref", "refs/heads/kan-1-x", base, evidence)
        assert pre_bash("git push --tags") == "" and head_commit(repo) == base
        git(repo, "update-ref", "refs/heads/kan-1-x", evidence, base)

        def post_push(command):
            with contextlib.redirect_stdout(io.StringIO()):
                cmd_post_tool({"cwd": repo, "tool_name": "Bash", "tool_input": {"command": command}})
        post_push("git push https://example.invalid/r.git HEAD")  # a URL: no tracking ref to tell, so kept
        assert head_commit(repo) == evidence, "dropped after a push to a URL"
        git(repo, "remote", "add", "origin", os.path.join(repo, "no-such-remote.git"))
        post_push("git push -u origin HEAD")
        assert head_commit(repo) == base and head_tree(repo) == tree, "a failed push left the evidence commit"
        assert drop_unpushed_evidence_commit(repo) is False
        # landed under another name (push_landed cannot see it): a remote branch holds it, so it stays
        bare = os.path.join(repo, "remote.git")
        git(repo, "init", "-q", "--bare", bare)
        git(repo, "remote", "set-url", "origin", bare)
        git(repo, "update-ref", "refs/heads/kan-1-x", evidence, base)
        git(repo, "push", "-q", "origin", "HEAD:refs/heads/elsewhere")
        git(repo, "fetch", "-q", "origin")
        post_push("git push -u origin HEAD")
        assert head_commit(repo) == evidence, "dropped an evidence commit a remote branch holds"
        ok = evidence_verdict(repo, evidence)
        assert ok["description"].startswith("evidence commit") and len(ok["description"]) <= CHECK_STATUS_MAX, ok
        long = evidence_verdict(repo, git(repo, "commit-tree", tree, "-p", evidence, "-m", evidence_message(
            dict(checks, counts="pytest 4710 passed, 3 skipped · vitest 1470 passed, 2 skipped · ui-kit 193 passed"),
            tree))[1])
        assert long["description"].startswith("evidence commit") and len(long["description"]) == CHECK_STATUS_MAX
    finally:
        if saved_remote is None:
            os.environ.pop("CLAUDE_CODE_REMOTE", None)
        else:
            os.environ["CLAUDE_CODE_REMOTE"] = saved_remote
        shutil.rmtree(repo, ignore_errors=True)
    # --- KAN-683: landing is one at a time - a push without current main is refused; a clean merge of
    # main carries the review and the screenshot, a conflicted or edited one does not; no network, no block
    repo = tempfile.mkdtemp(prefix="nexotec-selftest-land-")
    saved_remote = os.environ.pop("CLAUDE_CODE_REMOTE", None)
    try:
        bare, work = os.path.join(repo, "origin.git"), os.path.join(repo, "w")
        git(repo, "init", "-q", "--bare", bare)
        git(repo, "init", "-q", "-b", "main", work)
        for key, value in (("user.name", "t"), ("user.email", "t@example.invalid"), ("commit.gpgsign", "false")):
            git(work, "config", key, value)
        git(work, "remote", "add", "origin", bare)

        def land_commit(name, text):
            with open(os.path.join(work, name), "w") as fh:
                fh.write(text)
            git(work, "add", "-A")
            git(work, "commit", "-q", "-m", name)
            return head_commit(work)

        def main_moves(name, text):
            git(work, "switch", "-q", "main")
            git(work, "reset", "-q", "--hard", "origin/main")
            land_commit(name, text)
            git(work, "push", "-q", "origin", "main")
            git(work, "switch", "-q", "kan-1-x")

        def checked():
            update_state(work, lambda st: st.update({"checks": {"result": "pass", "tree": head_tree(work),
                                                                "lanes": ["config"], "not_run": [], "at": "t"}}))

        def pre_bash_at(command):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cmd_pre_bash({"cwd": work, "tool_input": {"command": command}})
            return out.getvalue()

        def post_bash_at(command):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cmd_post_tool({"cwd": work, "tool_name": "Bash", "tool_input": {"command": command}})
            return out.getvalue()
        land_commit("a.py", "a\n")
        git(work, "push", "-q", "origin", "main")
        git(work, "switch", "-q", "-c", "kan-1-x")
        ticket = land_commit("t.py", "ticket\n")
        checked()
        assert pre_bash_at("git push -u origin HEAD") == "", "a branch that contains main was refused"
        main_moves("m.py", "main moved\n")
        refused = pre_bash_at("git push -u origin HEAD")
        assert "deny" in refused and "does not contain origin/main" in refused, refused
        assert "git merge --no-edit origin/main" in refused and "scripts/dev/check" in refused, refused
        assert "does not contain origin/main" in "".join(push_problems(work, [])), "gate status does not say it"
        # reviewed and shown before the merge; a clean merge keeps the ticket's change, so both carry over
        update_state(work, lambda st: st.setdefault("events", []).extend([
            {"kind": "review", "verdict": "PASS", "tree": head_tree(work), "commit": ticket, "mode": "full"},
            {"kind": "screenshot", "tool": "file:x.png", "tree": head_tree(work)}]))
        git(work, "merge", "-q", "--no-edit", "origin/main")
        note = post_bash_at("git merge --no-edit origin/main")
        assert "carried over" in note and "scripts/dev/check" in note, note
        state = load_state(work)
        carried = latest(events(state, "evidence-carried"))
        assert (carried["from"], carried["to"]) == (ticket, head_commit(work)) and carried["review"] and \
            carried["screenshot"], carried
        review = latest([r for r in events(state, "review") if r.get("tree") == head_tree(work)])
        assert (review["verdict"], review["mode"], review["carried_from"]) == ("PASS", "carried", ticket), review
        assert latest([e for e in events(state, "screenshot") if e.get("tree") == head_tree(work)])
        assert carry_merge_evidence(work) is None, "the carry-over was recorded twice"
        assert "No passing local check" in pre_bash_at("git push -u origin HEAD"), "the check carried over the merge"
        checked()
        assert pre_bash_at("git push -u origin HEAD") == "", "a merged and checked branch was refused"
        # a conflicted merge carries nothing: its resolution is reviewed - and only the resolution, not the
        # 200 lines main gained (they would force a full review)
        main_moves("big.py", "x\n" * 200)
        main_moves("t.py", "main's own\n")
        git(work, "merge", "-q", "--no-edit", "origin/main")
        with open(os.path.join(work, "t.py"), "w") as fh:
            fh.write("resolved\n")
        git(work, "add", "-A")
        git(work, "commit", "-q", "--no-edit")
        assert "no evidence carried over" in post_bash_at("git commit --no-edit")
        missed = latest(events(load_state(work), "evidence-not-carried"))
        assert missed["to"] == head_commit(work) and "conflicts" in missed["reason"], missed
        assert not [r for r in events(load_state(work), "review") if r.get("tree") == head_tree(work)]
        first_parent = git(work, "rev-parse", "HEAD^1")[1]
        # the plain range holds main's 200 new lines, so it alone would need a full review
        assert "lines" in str(rereview_full_reason(git(work, "diff", "--numstat", first_parent, "HEAD")[1].splitlines()))
        assert rereview_refusal(work, load_state(work), first_parent, head_commit(work)) is None, "resolution not scoped"
        base, merge_info = rereview_base(work, first_parent, head_commit(work))
        assert merge_info and base == merge_info["result_tree"] and merge_info["conflicted"]
        assert rereview_base(work, ticket, head_commit(work))[1] is None  # not the first parent: the plain range
        # a merge edited by hand (clean, but its tree is not git's own result) carries nothing either
        main_moves("n.py", "n\n")
        git(work, "merge", "-q", "--no-commit", "--no-ff", "origin/main")
        with open(os.path.join(work, "extra.py"), "w") as fh:
            fh.write("slipped in\n")
        git(work, "add", "-A")
        git(work, "commit", "-q", "--no-edit")
        edited = carry_merge_evidence(work)
        assert edited["kind"] == "evidence-not-carried" and "edited" in edited["reason"], edited
        assert merge_carry_verdict(work, ticket) is None, "a plain commit read as a merge"

        def reviewed_here():
            update_state(work, lambda st: st.setdefault("events", []).extend([
                {"kind": "review", "verdict": "PASS", "tree": head_tree(work), "commit": head_commit(work),
                 "mode": "full"}, {"kind": "screenshot", "tool": "file:x.png", "tree": head_tree(work)}]))
        # under the hook's evidence commit (pushed after the merge), the merge below it still carries
        reviewed_here()
        main_moves("p.py", "p\n")
        git(work, "merge", "-q", "--no-edit", "origin/main")
        clean_merge = head_commit(work)
        checked()
        git(work, "update-ref", "refs/heads/kan-1-x", git(work, "commit-tree", head_tree(work), "-p", clean_merge, "-m",
                                                          evidence_message(load_state(work)["checks"], head_tree(work)))[1])
        assert head_commit(work) != clean_merge and merge_carry_verdict(work)["to"] == clean_merge
        on_evidence = carry_merge_evidence(work)
        assert on_evidence["kind"] == "evidence-carried" and on_evidence["to"] == clean_merge, on_evidence
        # a clean merge of another branch than main changes the three-dot diff: nothing carries
        reviewed_here()
        git(work, "switch", "-q", "-c", "side")
        land_commit("s.py", "side\n")
        git(work, "switch", "-q", "kan-1-x")
        git(work, "merge", "-q", "--no-ff", "--no-edit", "side")
        side_merge = carry_merge_evidence(work)
        assert side_merge["kind"] == "evidence-not-carried" and "is not the same" in side_merge["reason"], side_merge
        # a push in the same command line as a commit keeps its bookkeeping
        pushes_before = len(events(load_state(work), "push"))
        git(work, "push", "-q", "origin", "HEAD")
        git(work, "fetch", "-q", "origin")
        post_bash_at("git push -u origin HEAD; git commit --dry-run -m x")
        assert len(events(load_state(work), "push")) == pushes_before + 1, "the push was not recorded"
        # the network never blocks a push: a failed fetch judges by the last known origin/main and says so
        git(work, "remote", "set-url", "origin", os.path.join(repo, "gone.git"))
        checked()
        said = pre_bash_at("git push -u origin HEAD")
        assert "deny" not in said and "could not fetch origin/main" in said and "systemMessage" in said, said
    finally:
        if saved_remote is not None:
            os.environ["CLAUDE_CODE_REMOTE"] = saved_remote
        shutil.rmtree(repo, ignore_errors=True)
    print("hooks selftest: ok")
    return 0


# --------------------------------------------------------------------------

HOOKS = {
    "session-start": cmd_session_start,
    "session-sync": cmd_session_sync,
    "pre-bash": cmd_pre_bash,
    "pre-read": cmd_pre_read,
    "pre-edit": cmd_pre_edit,
    "pre-notion": cmd_pre_notion,
    "pre-render": cmd_pre_render,
    "post-tool": cmd_post_tool,
    "subagent-stop": cmd_subagent_stop,
    "stop": cmd_stop,
}


def main(argv):
    if len(argv) >= 1 and argv[0] == "cli":
        return cli(argv[1:])
    if not argv or argv[0] not in HOOKS:
        print("usage: nexotec_hooks.py <{}> | cli <command>".format("|".join(HOOKS)), file=sys.stderr)
        return 2
    try:
        HOOKS[argv[0]](read_input())
    except Exception:  # never block work because of a bug in a hook
        sys.stderr.write("nexotec hook '{}' failed (not blocking):\n{}".format(argv[0], traceback.format_exc()))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
