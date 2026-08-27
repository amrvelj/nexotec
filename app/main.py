from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.v1 import api_v1_router
from app.core.auth import get_jwks
from app.core.config import get_settings
from app.core.errors import NotFoundError, register_error_handlers
from app.core.observability import (
    ObservabilityMiddleware,
    configure_logging,
    configure_sentry,
    configure_tracing,
)

configure_logging()
configure_sentry()

app = FastAPI(title="DMS Platform", version="0.1.0")

configure_tracing(app)
register_error_handlers(app)
app.add_middleware(ObservabilityMiddleware)
# Holds the OIDC login transaction only — state/nonce/PKCE verifier across
# the redirect round trip to Zitadel and back (WP-4, app.platform.services.
# oidc). A DIFFERENT cookie from the dms_session one app.core.auth.py sets
# (name below, not SESSION_COOKIE_NAME) and deliberately SameSite=lax, not
# strict: this cookie must survive a top-level navigation *from*
# accounts.zitadel.cloud *back to* our own /auth/oidc/callback, which is
# exactly the cross-site top-level request a Strict cookie is dropped on —
# get this wrong and every login "fails" with a confusing state mismatch.
# dms_session itself stays Strict; it's never needed cross-site.
app.add_middleware(
    SessionMiddleware,
    secret_key=get_settings().session_secret_key,
    session_cookie="dms_oidc_txn",
    same_site="lax",
    max_age=600,
)
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
