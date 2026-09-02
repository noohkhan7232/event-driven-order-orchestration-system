from datetime import datetime
from decimal import Decimal
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import JobStatus, OrderStatus, PaymentStatus, ShipmentStatus, UserRole


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.CUSTOMER, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Warehouse(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "warehouses"

    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    region: Mapped[str] = mapped_column(String(80), nullable=False)

    inventory: Mapped[list["Inventory"]] = relationship(back_populates="warehouse")


class Inventory(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("sku", "warehouse_id", name="uq_inventory_sku_warehouse"),
        CheckConstraint("available_quantity >= 0", name="ck_inventory_available_non_negative"),
        CheckConstraint("reserved_quantity >= 0", name="ck_inventory_reserved_non_negative"),
        Index("ix_inventory_sku_available", "sku", "available_quantity"),
    )

    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str] = mapped_column(String(180), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    available_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=10)

    warehouse: Mapped[Warehouse] = relationship(back_populates="inventory")


class Order(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_orders_idempotency_key"),
        Index("ix_orders_customer_status", "customer_id", "status"),
        Index("ix_orders_created_status", "created_at", "status"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.CREATED, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)

    customer: Mapped[User] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    payments: Mapped[list["Payment"]] = relationship(back_populates="order")
    shipments: Mapped[list["Shipment"]] = relationship(back_populates="order")


class OrderItem(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "order_items"
    __table_args__ = (Index("ix_order_items_order_sku", "order_id", "sku"),)

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("orders.id"), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str] = mapped_column(String(180), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("warehouses.id"))

    order: Mapped[Order] = relationship(back_populates="items")


class Payment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (Index("ix_payments_order_status", "order_id", "status"),)

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("orders.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(60), default="simulation_gateway", nullable=False)
    provider_transaction_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.INITIATED, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(80))

    order: Mapped[Order] = relationship(back_populates="payments")


class Shipment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "shipments"
    __table_args__ = (Index("ix_shipments_order_status", "order_id", "status"),)

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("orders.id"), nullable=False)
    carrier: Mapped[str] = mapped_column(String(80), default="internal_fulfillment", nullable=False)
    tracking_number: Mapped[str | None] = mapped_column(String(120), unique=True)
    status: Mapped[ShipmentStatus] = mapped_column(Enum(ShipmentStatus), default=ShipmentStatus.PENDING, nullable=False)
    estimated_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[Order] = relationship(back_populates="shipments")
    events: Mapped[list["ShipmentEvent"]] = relationship(back_populates="shipment", cascade="all, delete-orphan")


class ShipmentEvent(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "shipment_events"

    shipment_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("shipments.id"), nullable=False)
    status: Mapped[ShipmentStatus] = mapped_column(Enum(ShipmentStatus), nullable=False)
    location: Mapped[str | None] = mapped_column(String(160))
    notes: Mapped[str | None] = mapped_column(Text)

    shipment: Mapped[Shipment] = relationship(back_populates="events")


class AuditLog(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)

    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class FailedJob(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "failed_jobs"
    __table_args__ = (Index("ix_failed_jobs_task_status", "task_name", "status"),)

    task_name: Mapped[str] = mapped_column(String(160), nullable=False)
    queue_name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.FAILED, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
