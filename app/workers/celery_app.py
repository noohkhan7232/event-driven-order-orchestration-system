from celery import Celery
from kombu import Exchange, Queue

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "distributed_order_processing",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

dead_letter_exchange = Exchange("dead_letter", type="direct")
domain_exchange = Exchange("domain_events", type="direct")

celery_app.conf.update(
    broker_transport_options={"confirm_publish": True},
    task_default_delivery_mode="persistent",
    task_default_exchange="domain_events",
    task_default_exchange_type="direct",
    task_default_routing_key="orders",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    task_routes={
        "app.workers.tasks.send_notifications": {"queue": "integrations"},
        "app.workers.tasks.deliver_webhooks": {"queue": "integrations"},
        "app.workers.tasks.expire_unpaid_orders": {"queue": "payments"},
        "app.workers.tasks.reconcile_pending_payments": {"queue": "payments"},
        "app.workers.tasks.process_refund": {"queue": "payments"},
        "app.workers.tasks.process_event": {"queue": "orders"},
        "app.workers.tasks.check_low_stock": {"queue": "inventory"},
        "app.workers.tasks.detect_delayed_shipments": {"queue": "shipments"},
    },
    task_queues=(
        Queue("orders", domain_exchange, routing_key="orders"),
        Queue("inventory", domain_exchange, routing_key="inventory"),
        Queue("payments", domain_exchange, routing_key="payments"),
        Queue("shipments", domain_exchange, routing_key="shipments"),
        Queue("integrations", domain_exchange, routing_key="integrations"),
        Queue("dead_letter", dead_letter_exchange, routing_key="dead_letter"),
    ),
    beat_schedule={
        "send-notifications": {
            "task": "app.workers.tasks.send_notifications",
            "schedule": settings.notification_interval_seconds,
            "options": {"queue": "integrations", "routing_key": "integrations"},
        },
        "deliver-webhooks": {
            "task": "app.workers.tasks.deliver_webhooks",
            "schedule": settings.webhook_delivery_interval_seconds,
            "options": {"queue": "integrations", "routing_key": "integrations"},
        },
        "expire-unpaid-orders": {
            "task": "app.workers.tasks.expire_unpaid_orders",
            "schedule": settings.payment_expiry_interval_seconds,
        },
        "reconcile-pending-payments": {
            "task": "app.workers.tasks.reconcile_pending_payments",
            "schedule": settings.payment_reconcile_interval_seconds,
        },
        "low-stock-scan-every-5-minutes": {
            "task": "app.workers.tasks.check_low_stock",
            "schedule": 300.0,
        },
        "shipment-delay-scan-every-10-minutes": {
            "task": "app.workers.tasks.detect_delayed_shipments",
            "schedule": 600.0,
        },
    },
)
