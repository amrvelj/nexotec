from fastapi import FastAPI

from app.api.v1 import api_v1_router
from app.core.errors import register_error_handlers

app = FastAPI(title="DMS Platform", version="0.1.0")

register_error_handlers(app)
app.include_router(api_v1_router)
