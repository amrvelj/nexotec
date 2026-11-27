"""Dumps the FastAPI app's own OpenAPI schema for frontend type generation.

Imports app.main directly rather than booting a server, so this runs in CI
without Postgres — app.openapi() needs the app object built, not a live
database (SQLAlchemy engines connect lazily). See KAN-35.
"""

import json
from pathlib import Path

from app.aftersales.public import service_order_grid_schema
from app.inventory.public import stock_item_grid_schema
from app.main import app

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "frontend" / "apps" / "dms" / "openapi.json"
# KAN-461: each list endpoint's served grid schema, as the SPA's architecture
# test reads it (grid-filters-are-applied.test.ts) without a running backend.
# tests/test_kan461_grid_schemas_file.py fails when it is stale.
GRID_SCHEMAS_PATH = OUTPUT_PATH.parent / "src" / "api" / "gridSchemas.json"
# Each through its context's `public` (ADR-047: a script reaches other contexts only there).
GRID_SCHEMAS = {
    "/v1/inventory/stock-items": stock_item_grid_schema,
    # KAN-848: the workshop order list.
    "/v1/service-orders": service_order_grid_schema,
}


def grid_schemas_document() -> str:
    schemas = {
        path: schema().model_dump(mode="json", by_alias=True) for path, schema in GRID_SCHEMAS.items()
    }
    return json.dumps(schemas, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    schema = app.openapi()
    OUTPUT_PATH.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {OUTPUT_PATH} ({len(schema['paths'])} paths, {len(schema['components']['schemas'])} schemas)")
    GRID_SCHEMAS_PATH.write_text(grid_schemas_document())
    print(f"Wrote {GRID_SCHEMAS_PATH} ({len(GRID_SCHEMAS)} grid schema(s))")


if __name__ == "__main__":
    main()
