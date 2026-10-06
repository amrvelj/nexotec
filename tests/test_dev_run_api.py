"""The desktop preview's API starts through scripts/dev/run-api (KAN-109).

On macOS the Claude desktop app launches preview servers through a hardened-runtime
binary, and dyld strips every DYLD_* variable at that exec, so a library path in
launch.json's `env` never reaches uvicorn and WeasyPrint fails to load Pango. The
wrapper sets the path after the launcher. These tests pin the wiring and run the
wrapper with a stand-in uvicorn, faking `uname` for the macOS branch.
"""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "scripts" / "dev" / "run-api"


def _api_configuration():
    launch = json.loads((ROOT / ".claude" / "launch.json").read_text())
    (api,) = [c for c in launch["configurations"] if c["name"] == "nexotec-api"]
    return api


def test_preview_api_starts_through_the_wrapper():
    api = _api_configuration()
    assert api["runtimeExecutable"] == "scripts/dev/run-api"
    assert api["runtimeArgs"] == ["--reload", "--port", "8000"]
    assert api["port"] == 8000


def test_launch_json_sets_no_dyld_variable():
    # dyld strips it before uvicorn runs; keeping it there only suggests it works.
    launch = json.loads((ROOT / ".claude" / "launch.json").read_text())
    for configuration in launch["configurations"]:
        assert not any(k.startswith("DYLD_") for k in configuration.get("env", {}))


def test_wrapper_is_executable():
    assert WRAPPER.stat().st_mode & stat.S_IXUSR


def _run_wrapper(tmp_path, uname, args=("--reload", "--port", "8000")):
    """Run a copy of the wrapper in a fake checkout whose uvicorn reports what it got."""
    checkout = tmp_path / "checkout"
    (checkout / "scripts" / "dev").mkdir(parents=True)
    (checkout / ".venv" / "bin").mkdir(parents=True)
    shutil.copy2(WRAPPER, checkout / "scripts" / "dev" / "run-api")
    fake_uvicorn = checkout / ".venv" / "bin" / "uvicorn"
    fake_uvicorn.write_text(
        '#!/bin/sh\necho "cwd=$(pwd)"\necho "dyld=${DYLD_FALLBACK_LIBRARY_PATH-unset}"\necho "args=$*"\n'
    )
    fake_uvicorn.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uname = bin_dir / "uname"
    fake_uname.write_text(f"#!/bin/sh\necho {uname}\n")
    fake_uname.chmod(0o755)

    env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD_")}
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["HOME"] = "/Users/someone"
    result = subprocess.run(
        [str(checkout / "scripts" / "dev" / "run-api"), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    lines = dict(line.split("=", 1) for line in result.stdout.splitlines())
    return checkout, lines


def test_wrapper_sets_the_library_path_on_macos(tmp_path):
    checkout, seen = _run_wrapper(tmp_path, "Darwin")
    assert seen["dyld"] == "/opt/homebrew/lib:/usr/local/lib:/Users/someone/lib:/usr/lib"
    assert seen["args"] == "app.main:app --reload --port 8000"
    assert Path(seen["cwd"]).resolve() == checkout.resolve()


def test_wrapper_leaves_linux_alone(tmp_path):
    checkout, seen = _run_wrapper(tmp_path, "Linux")
    assert seen["dyld"] == "unset"
    assert seen["args"] == "app.main:app --reload --port 8000"
    assert Path(seen["cwd"]).resolve() == checkout.resolve()


def test_wrapper_runs_without_arguments(tmp_path):
    # Written as ${1+"$@"} so macOS bash 3.2 does not call an empty "$@" unbound under set -u.
    _, seen = _run_wrapper(tmp_path, "Darwin", args=())
    assert seen["args"] == "app.main:app"
