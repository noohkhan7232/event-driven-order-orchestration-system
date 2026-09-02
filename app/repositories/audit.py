from app.models.entities import AuditLog, FailedJob
from app.repositories.base import Repository


class AuditLogRepository(Repository[AuditLog]):
    model = AuditLog


class FailedJobRepository(Repository[FailedJob]):
    model = FailedJob
