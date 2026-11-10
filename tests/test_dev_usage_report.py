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


# The review findings of 2026-11-10: a reviewer typed from its Agent call, growth beyond an
# item's size not pinned on the largest result, the opening estimate at the measured rate,
# and no sed script or heredoc printed as a file name.

SID_B = "bbbbbbbb-0000-0000-0000-000000000002"


def _call(mid, ctx, tools):
    return {"type": "assistant", "timestamp": "2026-11-03T09:00:00Z", "gitBranch": "kan-8-y",
            "message": {"id": mid, "model": "claude",
                        "usage": {"input_tokens": ctx, "cache_read_input_tokens": 0,
                                  "cache_creation_input_tokens": 0, "output_tokens": 0},
                        "content": [{"type": "tool_use", "id": f"{mid}-{i}", "name": name, "input": data}
                                    for i, (name, data) in enumerate(tools)]}}


def _results(mid, sizes, agent_id=None):
    record = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": f"{mid}-{i}", "content": ("x" * (size - len(MARKER))) + MARKER}
        for i, size in enumerate(sizes)]}}
    if agent_id:
        record["toolUseResult"] = {"agentId": agent_id, "status": "async_launched"}
    return record


def _write_session_b(folder):
    """Every call reads all of its input fresh and writes no output: each step's growth is
    exactly what its tool results added."""
    read = lambda name: ("Read", {"file_path": "/r/app/" + name})
    lines = [
        {"type": "attachment", "timestamp": "2026-11-03T08:59:59Z", "attachment": {"type": "instructions"},
         "rendered": [{"type": "text", "text": MARKER * (1000 // len(MARKER)) + "x" * (1000 % len(MARKER))}]},
        {"type": "user", "message": {"content": "<command-name>/ticket</command-name><command-args>KAN-8</command-args>"}},
        _call("c0", 10000, [read("r1.py")]), _results("c0", [1000]),
        _call("c1", 10500, [read("r2.py")]), _results("c1", [1000]),
        _call("c2", 11000, [read("r3.py")]), _results("c2", [1000]),
        _call("c3", 11500, [read("big.py"), ("ToolSearch", {"query": MARKER})]), _results("c3", [1400, 100]),
        _call("c4", 16500, [read("big2.py")]), _results("c4", [1400]),
        _call("c5", 21500, [("Agent", {"subagent_type": "reviewer", "prompt": MARKER})]),
        _results("c5", [100], agent_id="abc123"),
        _call("c6", 21550, [("Bash", {"command": f"sed -n 's/{MARKER}=x/y/p' app/d.py"})]), _results("c6", [100]),
        _call("c7", 21600, [("Bash", {"command": f"cat <<EOF\n{MARKER}.AG\nEOF"})]), _results("c7", [100]),
        _call("c8", 21650, []),
    ]
    (folder / (SID_B + ".jsonl")).write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    subagents = folder / SID_B / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc123.jsonl").write_text(json.dumps(  # no .meta.json beside it
        {"type": "assistant", "isSidechain": True, "message": {
            "id": "s1", "model": "claude", "usage": _usage(300, 20), "content": [{"type": "text", "text": MARKER}]}}) + "\n")


def _run_b(tmp_path):
    folder = tmp_path / "projects-b" / "-r"
    folder.mkdir(parents=True)
    _write_session_b(folder)
    return subprocess.run([sys.executable, str(SCRIPT), "--project-dir", str(folder), "--repo", "/r"],
                          capture_output=True, text=True, check=False)


def _breakdown(text, label):
    (line,) = [line for line in text.splitlines() if line.startswith("    " + label + " ")]
    return line.split()[-2]


def test_a_sub_agent_without_meta_is_typed_from_the_agent_call_that_started_it(tmp_path):
    result = _run_b(tmp_path)
    assert result.returncode == 0, result.stderr
    assert _row(result.stdout, "reviewer (1)")[-1] == "320"
    assert "sub-agent (1)" not in result.stdout


def test_growth_an_item_cannot_hold_goes_to_toolsearch_or_no_recorded_source(tmp_path):
    result = _run_b(tmp_path)
    lines = result.stdout.splitlines()
    start = lines.index("  largest single tool results (tokens added, x calls that carried them):")
    top = [line.split(None, 4)[1:] for line in lines[start + 1:start + 6]]
    # 1,400 characters hold at most 1,000 tokens: the 4,000 beyond are ToolSearch's schemas when
    # a ToolSearch ran in that step, and growth with no recorded source when none did.
    assert top == [
        ["4,000", "x5", "main", "ToolSearch"],
        ["1,000", "x5", "main", "Read app/big.py"],
        ["1,000", "x4", "main", "Read app/big2.py"],
        ["500", "x8", "main", "Read app/r1.py"],
        ["500", "x7", "main", "Read app/r2.py"],
    ]
    assert _breakdown(result.stdout, "growth with no recorded source") == "16,000"


def test_the_opening_context_is_estimated_at_the_measured_characters_per_token(tmp_path):
    # Three one-item steps measured 2.0 characters per token (and one 0.28): the median is 2.0,
    # so the 1,000-character CLAUDE.md before the first call is 500 tokens, carried by 9 calls.
    result = _run_b(tmp_path)
    assert _breakdown(result.stdout, "CLAUDE.md + MEMORY.md") == "4,500"


def test_a_sed_script_or_a_heredoc_is_never_printed_as_a_file_name(tmp_path):
    result = _run_b(tmp_path)
    assert MARKER not in result.stdout + result.stderr
    import importlib.machinery
    import importlib.util

    loader = importlib.machinery.SourceFileLoader("usage_report", str(SCRIPT))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("usage_report", loader))
    loader.exec_module(module)
    paths = module.Paths("/r")
    assert module.view_target("sed -n 's/password=hunter2/X/p' app/config.py", paths) == "app/config.py"
    assert module.view_target("sed -n '1,40p' /r/app/x.py", paths) == "app/x.py"
    assert module.view_target("cat <<EOF\nMueller.AG\nEOF", paths) == "?"
    assert module.view_target("head -n 20 notes.md 2>/dev/null", paths) == "notes.md"
