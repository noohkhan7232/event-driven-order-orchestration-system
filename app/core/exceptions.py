class DomainError(Exception):
    code = "domain_error"
    status_code = 400

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(DomainError):
    code = "not_found"
    status_code = 404


class ConflictError(DomainError):
    code = "conflict"
    status_code = 409


class BusinessError(ConflictError):
    """Expected rejection; never retried by workers."""


class InvalidTransitionError(BusinessError):
    code = "invalid_order_transition"


class TransientError(Exception):
    """Technical failure whose whole transaction can be retried."""


class AuthorizationError(DomainError):
    code = "forbidden"
    status_code = 403


class InsufficientInventoryError(BusinessError):
    code = "insufficient_inventory"


class IdempotencyConflictError(ConflictError):
    code = "idempotency_key_conflict"


class WarehouseCodeConflict(ConflictError):
    code = "warehouse_code_conflict"


class InventoryConflict(ConflictError):
    code = "inventory_conflict"
