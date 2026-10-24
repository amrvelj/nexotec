from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_v1_router
from app.core.auth import get_jwks
from app.core.config import get_settings
from app.core.errors import NotFoundError, register_error_handlers
from app.core.nul_guard import NulCharacterGuard
from app.core.object_store import get_object_store
from app.core.observability import (
    ObservabilityMiddleware,
    configure_logging,
    configure_sentry,
    configure_tracing,
)
from app.ownership_registry import register_ownership_providers
from app.platform.services.mailer import get_mailer
from app.platform.session_guard import enforce_live_session

configure_logging()
configure_sentry()
# KAN-200: every context's ownership provider, so the deactivation step can
# ask the registry (app.core.ownership) and import no context itself.
register_ownership_providers()

# KAN-196 (FR-AU-17/18): every request carrying a session token is checked
# against its `session` row — revoked, idle or past its absolute end is 401
# before any route runs. An app-level dependency, not middleware: it resolves
# the database through get_db like every route, so the tests' override
# reaches it, and it runs before the route's own dependencies.
app = FastAPI(title="DMS Platform", version="0.1.0", dependencies=[Depends(enforce_live_session)])

configure_tracing(app)
register_error_handlers(app)
# KAN-196: resolved here, not on the first invitation — a deployed service
# (anything but `development`, KAN-397) with the `logging` mail sender must
# fail its start, not its first reset.
get_mailer()
# KAN-200: the same for the object store — the local directory store is not
# a production store (KAN-402); the start fails, not the first signature.
get_object_store()
# KAN-387: a NUL in the path, query or body is a 422 before any route runs
# (Postgres text cannot hold one). Added before the observability middleware,
# so the refusal is logged and carries the correlation id. The login front
# door is exempt: its refusal must be the uniform 401 (FR-AU-07/16), and an
# address with a NUL names no account (identity.find_user_by_email).
app.add_middleware(NulCharacterGuard, exempt_paths=("/v1/auth/login",))
app.add_middleware(ObservabilityMiddleware)
app.include_router(api_v1_router)


# Root, unversioned — the standard discovery path (RFC 8414 / OIDC
# Discovery convention), not /v1/.well-known/... A verifier resolving JWKS
# generically has no reason to know this API is versioned at all.
@app.get("/.well-known/jwks.json", include_in_schema=False)
def jwks() -> dict:
    return get_jwks()

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
# when a built `frontend/apps/dms/dist` is actually present, so local
# API-only dev and the test suite (no frontend build step) are unaffected.
# Path moved from frontend/dist when frontend/ became an npm workspace
# (apps/dms + packages/ui-kit) — see frontend/package.json.
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "apps" / "dms" / "dist"
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
