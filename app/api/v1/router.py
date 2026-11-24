from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    auth,
    failed_jobs,
    health,
    inventory,
    notification_settings,
    orders,
    payment_config,
    payment_webhooks,
    products,
    public_tracking,
    shipments,
    streams,
    tenancy,
    webhook_endpoints,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(failed_jobs.router, prefix="/failed-jobs", tags=["Failed jobs"])
api_router.include_router(failed_jobs.admin_router, prefix="/admin/failed-jobs", tags=["Platform recovery"])
api_router.include_router(streams.router, prefix="/orders", tags=["Live orders"])
api_router.include_router(orders.router, prefix="/orders", tags=["Orders"])
api_router.include_router(inventory.router, prefix="/inventory", tags=["Inventory"])
api_router.include_router(shipments.router, prefix="/shipments", tags=["Shipments"])
api_router.include_router(shipments.settings_router, prefix="/settings/shipping", tags=["Shipping settings"])
api_router.include_router(health.router, prefix="/system", tags=["System"])

api_router.include_router(tenancy.router, tags=["Tenancy"])

api_router.include_router(products.router, prefix="/products", tags=["Catalog"])
api_router.include_router(admin.router, prefix="/admin/tenants", tags=["Platform administration"])
api_router.include_router(payment_config.router, prefix="/settings/payments", tags=["Payment settings"])
api_router.include_router(payment_webhooks.router, prefix="/webhooks/payments", tags=["Payment webhooks"])
api_router.include_router(webhook_endpoints.router, prefix="/webhook-endpoints", tags=["Seller webhooks"])
api_router.include_router(
    notification_settings.router, prefix="/settings/notifications", tags=["Notification settings"]
)
api_router.include_router(public_tracking.router, prefix="/public/track", tags=["Public tracking"])
api_router.include_router(streams.public_router, prefix="/public/track", tags=["Public tracking"])
