"""KAN-187: app.model_registry alone must put every mapped table on Base.metadata.

The registry exists so that Alembic's autogenerate (alembic/env.py), the
test schema (`create_all` in tests/conftest.py) and every script that
imports it see every table without importing app.main. A model module the
registry forgets is invisible to all three until something else happens to
import it — which is why this check runs in a fresh interpreter: inside the
test process, conftest and other tests have long since imported the whole
application, so a gap could not show.
"""

import json
import pathlib
import subprocess
import sys
import textwrap

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_PROBE = textwrap.dedent(
    """
    import importlib
    import json
    import pathlib

    import app
    import app.model_registry
    from app.db import Base

    def mapped_modules() -> set[str]:
        return {mapper.class_.__module__ for mapper in Base.registry.mappers}

    registered_tables = set(Base.metadata.tables)
    registered_modules = mapped_modules()

    root = pathlib.Path(app.__file__).resolve().parent
    candidates = sorted(
        [*root.glob("*/models/**/*.py"), *root.glob("core/*_model.py")],
        key=lambda path: path.as_posix(),
    )
    for path in candidates:
        if path.name == "__init__.py":
            continue
        module = ".".join(("app", *path.relative_to(root).with_suffix("").parts))
        importlib.import_module(module)

    print(
        json.dumps(
            {
                "candidates": len(candidates),
                "missing_modules": sorted(mapped_modules() - registered_modules),
                "missing_tables": sorted(set(Base.metadata.tables) - registered_tables),
            }
        )
    )
    """
)


def test_model_registry_alone_registers_every_mapped_model():
    probe = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr[-3000:]
    found = json.loads(probe.stdout.strip().splitlines()[-1])

    assert found["candidates"] >= 40, found  # the probe walked the model modules, not an empty glob
    assert found["missing_modules"] == [], (
        "these modules define a mapped class but app.model_registry does not import them "
        f"(tables Base.metadata lacks after `import app.model_registry`: {found['missing_tables']}): "
        f"{found['missing_modules']}"
    )
    assert found["missing_tables"] == [], found
