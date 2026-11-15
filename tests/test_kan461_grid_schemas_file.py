"""KAN-461: the SPA's copy of each served grid schema
(`frontend/apps/dms/src/api/gridSchemas.json`) is the backend's, so its
architecture test judges the real registry. Regenerate with
`make generate-frontend-types`."""

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _generator():
    spec = importlib.util.spec_from_file_location("generate_openapi_schema", _ROOT / "scripts" / "generate_openapi_schema.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_spas_grid_schemas_file_is_the_backends():
    generator = _generator()
    assert generator.GRID_SCHEMAS_PATH.read_text() == generator.grid_schemas_document(), (
        "frontend/apps/dms/src/api/gridSchemas.json is stale — run `make generate-frontend-types`"
    )
