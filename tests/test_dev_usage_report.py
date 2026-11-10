"""scripts/dev/usage-report reads Claude Code's session transcripts and prints what a session
cost (KAN-613): per agent the API calls and tokens, the main session's input split by what it
carried, and the five largest tool results. It prints only counts and file names - never what
a transcript says - and opens no network connection.

These tests build a transcript with known token counts, every content field carrying a marker,
and run the script on it in a subprocess whose sockets refuse to connect. The script's own
`--selftest` (run by scripts/dev/check's config lane) checks the attribution arithmetic on a
second fixture.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "dev" / "usage-report"
MARKER = "TRANSCRIPT-CONTENT-7f3a"
SID = "aaaaaaaa-0000-0000-0000-000000000001"

NO_NETWORK = """
import socket
def _refuse(*args, **kwargs):
    raise SystemExit("usage-report opened a network connection")
socket.socket.connect = _refuse
socket.create_connection = _refuse
"""


def _usage(ctx, out):
    return {"input_tokens": 1, "cache_read_input_tokens": ctx - 1 - 50, "cache_creation_input_tokens": 50,
            "output_tokens": out}


def _assistant(mid, ctx, tool):
    """One API call, written the way Claude Code writes it: a thinking record and a tool record."""
    message = {"id": mid, "model": "claude", "usage": _usage(ctx, 10)}
    return [
        {"type": "assistant", "timestamp": "2026-11-02T09:00:00Z", "gitBranch": "kan-7-x",
         "message": dict(message, content=[{"type": "thinking", "thinking": MARKER}])},
        {"type": "assistant", "timestamp": "2026-11-02T09:00:01Z", "gitBranch": "kan-7-x",
         "message": dict(message, content=[{"type": "tool_use", "id": "t-" + mid, "name": tool[0],
                                            "input": tool[1]}])},
    ]


def _result(mid):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t-" + mid, "content": (MARKER + " ") * 50}]}}


def _write_session(folder):
    steps = [  # (context the call reads, the tool it calls)
        (1000, ("Read", {"file_path": "/r/app/a.py"})),
        (1110, ("Read", {"file_path": "/r/.claude/worktrees/w/app/b.py"})),
        (1320, ("Bash", {"command": "cat /r/app/c.py  # " + MARKER})),
        (1630, ("mcp__x__notion-fetch", {"id": "https://app.notion.com/p/" + "b" * 32})),
        (2040, ("Bash", {"command": "pytest -q tests/" + MARKER})),
        (2550, ("Bash", {"command": "echo " + MARKER})),
        (2610, ("Bash", {"command": "true"})),
    ]
    lines = [{"type": "user", "timestamp": "2026-11-02T08:59:59Z",
              "message": {"content": "<command-name>/ticket</command-name>\n<command-args>KAN-7</command-args>\n"
                                     + MARKER}}]
    for i, (ctx, tool) in enumerate(steps):
        if i:
            lines.append(_result(f"m{i - 1}"))
        lines += _assistant(f"m{i}", ctx, tool)
    (folder / (SID + ".jsonl")).write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    subagents = folder / SID / "subagents"
    subagents.mkdir(parents=True)
    for name, kind, ctx, out in (("agent-r", "reviewer", 300, 20), ("agent-e", "Explore", 100, 5)):
        records = [{"type": "user", "isSidechain": True, "message": {"content": MARKER}},
                   {"type": "assistant", "isSidechain": True, "message": {
                       "id": name, "model": "claude", "usage": _usage(ctx, out),
                       "content": [{"type": "text", "text": MARKER}]}}]
        (subagents / (name + ".jsonl")).write_text("\n".join(json.dumps(x) for x in records) + "\n")
        (subagents / (name + ".meta.json")).write_text(json.dumps({"agentType": kind, "description": MARKER}))


def _run(tmp_path, *args):
    folder = tmp_path / "projects" / "-r"
    folder.mkdir(parents=True)
    _write_session(folder)
    (tmp_path / "sitecustomize.py").write_text(NO_NETWORK)
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    return subprocess.run([sys.executable, str(SCRIPT), "--project-dir", str(folder), "--repo", "/r", *args],
                          capture_output=True, text=True, env=env, cwd=str(tmp_path), check=False)


def _row(text, name):
    (line,) = [line for line in text.splitlines() if line.startswith("  " + name + " ")]
    return line[len("  " + name):].split()


def test_selftest_passes():
    result = subprocess.run([sys.executable, str(SCRIPT), "--selftest"], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, check=False)
    assert result.returncode == 0, result.stdout
    assert "selftest: ok" in result.stdout


def test_counts_each_call_once_and_splits_main_from_sub_agents(tmp_path):
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    # calls, input, cache-read, cache-write, output, total
    assert _row(result.stdout, "main") == ["7", "7", "11,903", "350", "70", "12,330"]
    assert _row(result.stdout, "reviewer (1)") == ["1", "1", "249", "50", "20", "320"]
    assert _row(result.stdout, "Explore (1)") == ["1", "1", "49", "50", "5", "105"]
    assert _row(result.stdout, "all agents")[-1] == "12,755"
    assert "## KAN-7 - 2026-11-02 - session aaaaaaaa - branch kan-7-x" in result.stdout


def test_lists_the_five_largest_tool_results_by_name_only(tmp_path):
    result = _run(tmp_path)
    lines = result.stdout.splitlines()
    start = lines.index("  largest single tool results (tokens added, x calls that carried them):")
    top = [line.split(None, 4)[1:] for line in lines[start + 1:start + 6]]
    assert top == [
        ["500", "x2", "main", "Bash (pytest)"],
        ["400", "x3", "main", "notion-fetch " + "b" * 32],
        ["300", "x4", "main", "Bash view app/c.py"],
        ["200", "x5", "main", "Read app/b.py"],
        ["100", "x6", "main", "Read app/a.py"],
    ]
    assert "Bash (echo)" not in result.stdout  # the sixth, smallest result


def test_prints_no_transcript_content_and_opens_no_connection(tmp_path):
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr  # sitecustomize turns a connection into an exit
    assert MARKER not in result.stdout + result.stderr


def test_a_folder_without_a_ticket_session_says_so(tmp_path):
    result = _run(tmp_path, "--exclude", "aaaaaaaa")
    assert result.returncode == 1
    assert "No matching session." in result.stdout
