from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_v1_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers

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
