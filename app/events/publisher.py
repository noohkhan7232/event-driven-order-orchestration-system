from app.core.logging import get_logger
from app.events.contracts import DomainEvent
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


class EventPublisher:
    def publish(self, event: DomainEvent, queue: str) -> None:
        logger.info("event_published", event_type=event.event_type, queue=queue, aggregate_id=str(event.aggregate_id))
        celery_app.send_task("app.workers.tasks.process_event", args=[event.model_dump(mode="json")], queue=queue)
