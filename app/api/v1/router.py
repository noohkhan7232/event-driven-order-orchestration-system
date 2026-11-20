from fastapi import APIRouter

from app.api.v1.endpoints import auth, health, inventory, orders, shipments, tenancy

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(orders.router, prefix="/orders", tags=["Orders"])
api_router.include_router(inventory.router, prefix="/inventory", tags=["Inventory"])
api_router.include_router(shipments.router, prefix="/shipments", tags=["Shipments"])
api_router.include_router(health.router, prefix="/system", tags=["System"])

api_router.include_router(tenancy.router, tags=["Tenancy"])
