"""Dumps the FastAPI app's own OpenAPI schema for frontend type generation.

Imports app.main directly rather than booting a server, so this runs in CI
without Postgres — app.openapi() needs the app object built, not a live
database (SQLAlchemy engines connect lazily). See KAN-35.
"""

import json
from pathlib import Path

from app.main import app

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "frontend" / "apps" / "dms" / "openapi.json"


def main() -> None:
    schema = app.openapi()
    OUTPUT_PATH.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {OUTPUT_PATH} ({len(schema['paths'])} paths, {len(schema['components']['schemas'])} schemas)")


if __name__ == "__main__":
    main()
