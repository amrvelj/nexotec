from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_v1_router
from app.core.config import get_settings
from app.core.errors import NotFoundError, register_error_handlers

app = FastAPI(title="DMS Platform", version="0.1.0")

register_error_handlers(app)
app.include_router(api_v1_router)

# Credentialed CORS for the frontend dev server (issue #8 cookie session) —
# allow_credentials requires an explicit origin list, not "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Staging/prod single-origin fallback: serve the built frontend from this
# same FastAPI service so the SameSite=strict session cookie (app/api/v1/
# auth.py) stays valid — a separate static-site origin would be cross-site
# to the browser and the cookie would silently never be sent. Only mounted
# when a built `frontend/dist` is actually present, so local API-only dev
# and the test suite (no frontend build step) are unaffected.
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_catch_all(full_path: str) -> FileResponse:
        # SPA client-side routing: any non-API, non-asset path resolves to
        # index.html and React Router takes it from there. `include_router`'s
        # prefix isn't a hard boundary in Starlette — it flattens routes and
        # keeps evaluating — so an unmatched /v1/* path would otherwise fall
        # through to here and "succeed" with an HTML page instead of 404ing.
        # Excluded explicitly so a typo'd API call still gets the same JSON
        # error shape as every other 404 in this API.
        if full_path == "v1" or full_path.startswith("v1/"):
            raise NotFoundError(f"No route found for /{full_path}.")
        return FileResponse(_FRONTEND_DIST / "index.html")
