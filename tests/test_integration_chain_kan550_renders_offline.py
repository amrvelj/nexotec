"""KAN-174 for KAN-550's integration-chain migration `db5f1414cbce`: `alembic
upgrade 3b3faa39c5e2:db5f1414cbce --sql` renders, exit 0, with no database.
Its row check (it reads rows) is skipped with a comment that says so; the
CHECK's DDL renders with the four remaining slots, never `aes_key`.
"""

import os
import pathlib
import subprocess
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_kan550_slot_migration_renders_offline():
    run = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "3b3faa39c5e2:db5f1414cbce", "--sql"],
        cwd=_REPO_ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert run.returncode == 0, run.stderr[-3000:]
    assert "-- KAN-550 'aes_key' rows check skipped" in run.stdout
    assert "DROP CONSTRAINT ck_integration_secret_ref_slot_enum" in run.stdout
    added = [line for line in run.stdout.splitlines() if "ADD CONSTRAINT ck_integration_secret_ref_slot_enum" in line]
    expected = (
        "ALTER TABLE integration_secret_ref ADD CONSTRAINT ck_integration_secret_ref_slot_enum "
        "CHECK (\"slot\" IN ('password', 'client_secret', 'refresh_token', 'certificate'));"
    )
    assert added == [expected]
