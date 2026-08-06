from fastapi import APIRouter

from app.api.v1.dealers import router as dealers_router
from app.api.v1.health import router as health_router
from app.api.v1.reference_data import router as reference_data_router
from app.api.v1.vehicles import router as vehicles_router

api_v1_router = APIRouter(prefix="/v1")
api_v1_router.include_router(health_router)
api_v1_router.include_router(dealers_router)
api_v1_router.include_router(reference_data_router)
api_v1_router.include_router(vehicles_router)
