"""Router composition root: assembles every context's API router under
/v1. Not itself part of any bounded context, so it's the one place allowed
to import each context's api package directly rather than through its
public.py — it needs the APIRouter objects, not domain functions.
"""

from fastapi import APIRouter

from app.customer.api.customers import router as customers_router
from app.platform.api.auth import router as auth_router
from app.platform.api.dealer_groups import router as dealer_groups_router
from app.platform.api.dealerships import router as dealerships_router
from app.platform.api.health import router as health_router
from app.platform.api.reference_data import router as reference_data_router
from app.platform.api.user_preferences import router as user_preferences_router
from app.sales.api.transactions import router as transactions_router
from app.vehicle.api.catalogue_admin import router as vehicle_catalogue_admin_router
from app.vehicle.api.lookup import router as vehicle_lookup_router
from app.vehicle.api.vehicles import router as vehicles_router

api_v1_router = APIRouter(prefix="/v1")
api_v1_router.include_router(health_router)
api_v1_router.include_router(dealerships_router)
api_v1_router.include_router(dealer_groups_router)
api_v1_router.include_router(reference_data_router)
api_v1_router.include_router(vehicles_router)
api_v1_router.include_router(vehicle_lookup_router)
api_v1_router.include_router(vehicle_catalogue_admin_router)
api_v1_router.include_router(customers_router)
api_v1_router.include_router(transactions_router)
api_v1_router.include_router(auth_router)
api_v1_router.include_router(user_preferences_router)
