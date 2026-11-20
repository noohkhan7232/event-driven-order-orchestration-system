from sqlalchemy.orm import Session

from app.events.contracts import DomainEvent
from app.models.entities import OutboxEvent


class OutboxRepository:
    def __init__(self, db: Session):
        self.db = db

    def add(self, event: DomainEvent, queue: str) -> OutboxEvent:
        row = OutboxEvent(
            event_id=event.event_id,
            tenant_id=event.tenant_id,
            aggregate_id=event.aggregate_id,
            event_type=event.event_type,
            queue=queue,
            payload=event.model_dump(mode="json"),
            correlation_id=event.correlation_id,
        )
        self.db.add(row)
        return row
