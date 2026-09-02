from enum import StrEnum


class UserRole(StrEnum):
    CUSTOMER = "customer"
    ADMIN = "admin"
    VENDOR = "vendor"


class OrderStatus(StrEnum):
    CREATED = "created"
    VALIDATED = "validated"
    INVENTORY_RESERVED = "inventory_reserved"
    PAYMENT_PENDING = "payment_pending"
    PAID = "paid"
    FULFILLMENT_REQUESTED = "fulfillment_requested"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


class PaymentStatus(StrEnum):
    INITIATED = "initiated"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    REFUNDED = "refunded"


class ShipmentStatus(StrEnum):
    PENDING = "pending"
    CREATED = "created"
    IN_TRANSIT = "in_transit"
    DELAYED = "delayed"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    FAILED = "failed"
    COMPLETED = "completed"
