from fastapi import APIRouter

from app.api.v1.endpoints import admin, auth, health, inventory, orders, payment_config, products, shipments, tenancy

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(orders.router, prefix="/orders", tags=["Orders"])
api_router.include_router(inventory.router, prefix="/inventory", tags=["Inventory"])
api_router.include_router(shipments.router, prefix="/shipments", tags=["Shipments"])
api_router.include_router(health.router, prefix="/system", tags=["System"])

api_router.include_router(tenancy.router, tags=["Tenancy"])

api_router.include_router(products.router, prefix="/products", tags=["Catalog"])
api_router.include_router(admin.router, prefix="/admin/tenants", tags=["Platform administration"])
api_router.include_router(payment_config.router, prefix="/settings/payments", tags=["Payment settings"])
