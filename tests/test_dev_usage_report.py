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
    """One API call with its final usage (a stop reason), reading `ctx` fresh, writing nothing."""
    return {"type": "assistant", "timestamp": "2026-11-03T09:00:00Z", "gitBranch": "kan-8-y",
            "message": {"id": mid, "model": "claude", "stop_reason": "tool_use",
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
    exactly what its tool results added. Twelve calls, c0 to c11."""
    def read(name):
        return "Read", {"file_path": "/r/app/" + name}

    lines = [
        {"type": "attachment", "timestamp": "2026-11-03T08:59:59Z", "attachment": {"type": "instructions"},
         "rendered": [{"type": "text", "text": MARKER * (1000 // len(MARKER)) + "x" * (1000 % len(MARKER))}]},
        {"type": "user", "message": {"content": "<command-name>/ticket</command-name><command-args>KAN-8</command-args>"}},
        # five one-item steps at 2.0 characters per token: the session's measured rate
        _call("c0", 10000, [read("r1.py")]), _results("c0", [1000]),
        _call("c1", 10500, [read("r2.py")]), _results("c1", [1000]),
        _call("c2", 11000, [read("r3.py")]), _results("c2", [1000]),
        _call("c3", 11500, [read("r4.py")]), _results("c3", [1000]),
        _call("c4", 12000, [read("r5.py")]), _results("c4", [1000]),
        # a read beside a ToolSearch, then a read alone, each step growing by 5,000
        _call("c5", 12500, [read("big.py"), ("ToolSearch", {"query": MARKER})]), _results("c5", [1400, 100]),
        _call("c6", 17500, [read("big2.py")]), _results("c6", [1400]),
        _call("c7", 22500, [("Agent", {"subagent_type": "reviewer", "prompt": MARKER})]),
        _results("c7", [100], agent_id="abc123"),
        _call("c8", 22550, [("Bash", {"command": f'cd /r\ngit commit -m "Fix it; {MARKER} asked"'})]),
        _results("c8", [100]),
        _call("c9", 22600, [("Bash", {"command": f"sed -n 's/{MARKER}=x/y/p' app/d.py"})]), _results("c9", [100]),
        _call("c10", 22650, [("Bash", {"command": f"cd /r\ncat <<'EOF' > notes.txt\nhi; cat {MARKER}.AG\nEOF"})]),
        _results("c10", [100]),
        _call("c11", 22700, []),
    ]
    (folder / (SID_B + ".jsonl")).write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    subagents = folder / SID_B / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc123.jsonl").write_text(json.dumps(  # no .meta.json beside it
        {"type": "assistant", "isSidechain": True, "message": {
            "id": "s1", "model": "claude", "stop_reason": "end_turn", "usage": _usage(300, 20),
            "content": [{"type": "text", "text": MARKER}]}}) + "\n")


def _run_b(tmp_path):
    folder = tmp_path / "projects-b" / "-r"
    folder.mkdir(parents=True)
    _write_session_b(folder)
    return subprocess.run([sys.executable, str(SCRIPT), "--project-dir", str(folder), "--repo", "/r"],
                          capture_output=True, text=True, check=False)


def _breakdown(text, label):
    (line,) = [line for line in text.splitlines() if line.startswith("    " + label + " ")]
    return line.split()[-2]


def _module():
    import importlib.machinery
    import importlib.util

    loader = importlib.machinery.SourceFileLoader("usage_report", str(SCRIPT))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("usage_report", loader))
    loader.exec_module(module)
    return module


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
    # Beside a ToolSearch, 1,400 characters hold 700 tokens at the session's measured 2.0: the
    # 4,300 beyond are the schemas it loaded. Alone they hold at most 1,000 (one token per 1.4
    # characters): the 4,000 beyond are growth with no recorded source.
    assert top == [
        ["4,300", "x6", "main", "ToolSearch"],
        ["1,000", "x5", "main", "Read app/big2.py"],
        ["700", "x6", "main", "Read app/big.py"],
        ["500", "x11", "main", "Read app/r1.py"],
        ["500", "x10", "main", "Read app/r2.py"],
    ]
    assert _breakdown(result.stdout, "growth with no recorded source") == "20,000"


def test_the_opening_context_is_estimated_at_the_measured_characters_per_token(tmp_path):
    # Five one-item steps measured 2.0 characters per token (and one 0.28): the median is 2.0,
    # so the 1,000-character CLAUDE.md before the first call is 500 tokens, carried by 12 calls.
    result = _run_b(tmp_path)
    assert _breakdown(result.stdout, "CLAUDE.md + MEMORY.md") == "6,000"


def test_no_word_of_a_command_becomes_a_label(tmp_path):
    result = _run_b(tmp_path)
    assert MARKER not in result.stdout + result.stderr
    module = _module()
    paths = module.Paths("/r")

    def label(command):
        return module.classify_tool("Bash", {"command": command}, paths)

    # a `cd` on its own line, then a `;` inside a commit message, an inline script or a heredoc
    assert label('cd /r\ngit commit -m "Fix it; Mueller asked"') == ("shell", "Bash (git)")
    assert label("cd /r\npython3 - <<'EOF'\nx = 1; customer_name = 2\nEOF") == ("shell", "Bash (python3)")
    assert label("cd /r\ncat <<'EOF' > notes.txt\nhello; cat Mueller.AG\nEOF") == ("read", "Bash view ?")
    # a program not on the known list is "other", whatever its first word
    assert label("Mueller --verbose") == ("shell", "Bash (other)")
    # sed scripts, less commands and heredocs are never file names
    assert label("sed -n 's/password=hunter2/X/p' app/config.py") == ("read", "Bash view app/config.py")
    assert label("sed -n -e 'y/abc/xyz/' app/f.py") == ("read", "Bash view app/f.py")
    assert label("less +/Mueller app/y.py") == ("read", "Bash view app/y.py")
    assert label("sed -n '1,40p' /r/app/x.py") == ("read", "Bash view app/x.py")
    assert label("head -n 20 notes.md 2>/dev/null") == ("read", "Bash view notes.md")
    # an option's value is never the file, and `&` separates commands
    assert label("less -p anto@example.com app/y.py") == ("read", "Bash view app/y.py")
    assert label("less -P X.AG app/y.py") == ("read", "Bash view app/y.py")
    assert label("nl -s X.AG app/y.py") == ("read", "Bash view app/y.py")
    assert label("head -c 1.5k app/x.py") == ("read", "Bash view app/x.py")
    assert label("cat x & git config user.email anto@example.com") == ("read", "Bash view ?")
    assert label("tail -f /tmp/x.log") == ("read", "Bash view <outside repo>/x.log")
    assert label("cat -e app/x.py") == ("read", "Bash view app/x.py")
    # a line that only assigns variables is skipped like a bare `cd`
    assert label("W=/x\ncat app/a.py") == ("read", "Bash view app/a.py")
    assert module.Paths("/repo").show("/repo/../x") == "<outside repo>/x"
    # what a command carries does not classify it: a commit message naming pytest is not a test run
    assert label("git commit -m 'run pytest later'") == ("shell", "Bash (git)")
    assert label("cd /r && DMS_X=1 pytest -q") == ("tests", "Bash (pytest)")
    assert label("gh api 'repos/o/r/commits/abc/check-runs' --jq '.check_runs[] | .name'") == ("ci", "Bash (CI poll)")


def test_a_call_without_a_final_usage_record_is_reported_as_a_lower_bound(tmp_path):
    # Session A's calls carry no stop reason (a streaming snapshot, as in older transcripts);
    # session B's carry one.
    a = _run(tmp_path, "--summary-only")
    assert "| aaaaaaaa | 2026-11-02 | KAN-7 | 12,755 * |" in a.stdout  # marked in the summary too
    assert "* Some calls recorded only a streaming snapshot" in a.stdout
    assert "main: 7 of 7 call(s) recorded only a streaming snapshot of their usage" in _run(tmp_path / "full").stdout
    b = _run_b(tmp_path)
    assert "streaming snapshot" not in b.stdout and " * |" not in b.stdout


def test_the_measured_rate_ignores_a_small_reminder_beside_the_item():
    # Older transcripts add a token-count reminder to nearly every step; a step whose only
    # other item is 2% of the result or less still measures the rate.
    module = _module()
    stream = module.Stream("main", module.Paths("/r"))
    ctx = 10000
    for i in range(6):
        stream.add(_call(f"c{i}", ctx, [("Read", {"file_path": f"/r/app/{i}.py"})]))
        stream.add(_results(f"c{i}", [1000]))
        stream.add({"type": "attachment", "attachment": {"type": "total_tokens_reminder"},
                    "rendered": [{"type": "text", "text": "y" * 20}]})
        ctx += 500
    stream.add(_call("c6", ctx, []))
    stream.finish()
    assert stream.chars_per_token == 2.0


def test_fewer_than_five_measured_steps_fall_back_to_the_default_rate():
    module = _module()
    stream = module.Stream("main", module.Paths("/r"))
    ctx = 10000
    for i in range(4):  # four one-item steps at 2.0: too few to trust
        stream.add(_call(f"c{i}", ctx, [("Read", {"file_path": f"/r/app/{i}.py"})]))
        stream.add(_results(f"c{i}", [1000]))
        ctx += 500
    stream.add(_call("c4", ctx, []))
    stream.finish()
    assert stream.chars_per_token == module.CHARS_PER_TOKEN == 2.25
