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
    task_default_exchange="domain_events",
    task_default_exchange_type="direct",
    task_default_routing_key="orders",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    task_routes={
        "app.workers.tasks.process_event": {"queue": "orders"},
        "app.workers.tasks.check_low_stock": {"queue": "inventory"},
        "app.workers.tasks.detect_delayed_shipments": {"queue": "shipments"},
    },
    task_queues=(
        Queue("orders", domain_exchange, routing_key="orders"),
        Queue("inventory", domain_exchange, routing_key="inventory"),
        Queue("payments", domain_exchange, routing_key="payments"),
        Queue("shipments", domain_exchange, routing_key="shipments"),
        Queue("dead_letter", dead_letter_exchange, routing_key="dead_letter"),
    ),
    beat_schedule={
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
