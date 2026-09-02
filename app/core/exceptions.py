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


class AuthorizationError(DomainError):
    code = "forbidden"
    status_code = 403


class InsufficientInventoryError(ConflictError):
    code = "insufficient_inventory"
