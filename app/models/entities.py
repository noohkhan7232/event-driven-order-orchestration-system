import re
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship, validates

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import JobStatus, OrderStatus, PaymentStatus, ShipmentStatus, UserRole

DEFAULT_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class OrderTimeline(Base):
    __tablename__ = "order_timeline"
    __table_args__ = (
        Index("ix_order_timeline_tenant_id", "tenant_id", "id"),
        Index("ix_order_timeline_order_id", "order_id", "id"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("orders.id"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Tenant(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "tenants"
    __table_args__ = (CheckConstraint("status IN ('active', 'suspended')", name="ck_tenant_status"),)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    default_currency: Mapped[str] = mapped_column(String(3), default="INR", server_default="INR", nullable=False)

    @validates("slug")
    def validate_slug(self, key: str, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,39}", value) or value in {
            "admin",
            "api",
            "www",
            "app",
            "system",
            "health",
            "metrics",
        }:
            raise ValueError("Invalid or reserved tenant slug")
        return value


class Product(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_sku"),
        CheckConstraint("price >= 0", name="ck_product_price_non_negative"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)


class ApiKey(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "api_keys"
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(24), unique=True, index=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "(is_platform_admin AND tenant_id IS NULL) OR (NOT is_platform_admin AND tenant_id IS NOT NULL)",
            name="ck_users_tenant_principal",
        ),
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("tenants.id"))
    is_platform_admin: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.CUSTOMER, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")

    def validate_tenancy(self) -> None:
        if bool(self.is_platform_admin) != (self.tenant_id is None):
            raise ValueError("Platform admins must have no tenant; every other user requires a tenant")


class Warehouse(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_warehouses_tenant_identity"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)

    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    region: Mapped[str] = mapped_column(String(80), nullable=False)

    inventory: Mapped[list["Inventory"]] = relationship(back_populates="warehouse")


class Inventory(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", "warehouse_id", name="uq_inventory_tenant_identity"),
        CheckConstraint("available_quantity >= 0", name="ck_inventory_available_non_negative"),
        CheckConstraint("reserved_quantity >= 0", name="ck_inventory_reserved_non_negative"),
        Index("ix_inventory_sku_available", "sku", "available_quantity"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
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
        UniqueConstraint("tenant_id", "principal_scope", "idempotency_key", name="uq_orders_tenant_principal_key"),
        Index("ix_orders_tenant_created", "tenant_id", "created_at"),
        Index("ix_orders_customer_status", "customer_id", "status"),
        Index("ix_orders_created_status", "created_at", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
    principal_scope: Mapped[str] = mapped_column(String(50), nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(100))
    buyer_name: Mapped[str | None] = mapped_column(String(120))
    buyer_email: Mapped[str | None] = mapped_column(String(255))
    buyer_phone: Mapped[str | None] = mapped_column(String(20))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.CREATED, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    inventory_reserved: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    payment_method: Mapped[str] = mapped_column(String(12), default="prepaid", server_default="prepaid", nullable=False)
    payment_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def payment_status(self) -> str | None:
        payment = max(self.payments, key=lambda p: (p.created_at, p.id), default=None)
        return (payment.provider_status or payment.status.value) if payment else None

    @property
    def checkout(self) -> dict | None:
        if self.payment_method == "prepaid" and self.status == OrderStatus.PAYMENT_PENDING:
            for payment in self.payments:
                if payment.status == PaymentStatus.INITIATED and payment.checkout_payload:
                    return {**payment.checkout_payload, "expires_at": self.payment_expires_at}
        return None

    customer: Mapped[User | None] = relationship(back_populates="orders")
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


class TenantPaymentConfig(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "tenant_payment_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", name="uq_tenant_payment_provider"),
        CheckConstraint("provider = 'razorpay'", name="ck_payment_config_provider"),
        CheckConstraint("mode IN ('test', 'live')", name="ck_payment_config_mode"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    key_id: Mapped[str] = mapped_column(String(120), nullable=False)
    key_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    webhook_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    webhook_public_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)


class Payment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (Index("ix_payments_order_status", "order_id", "status"),)

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("orders.id"), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(40), default="simulation_gateway", nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(String(120))
    provider_transaction_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.INITIATED, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    provider_status: Mapped[str | None] = mapped_column(String(40))
    checkout_payload: Mapped[dict | None] = mapped_column(JSON)

    order: Mapped[Order] = relationship(back_populates="payments")


class Shipment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "shipments"
    __table_args__ = (Index("ix_shipments_order_status", "order_id", "status"),)

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("orders.id"), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(40))
    provider_reference: Mapped[str | None] = mapped_column(String(120))
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


class Refund(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint("status IN ('requested','processing','succeeded','failed')", name="ck_refund_status"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("orders.id"), nullable=False)
    payment_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("payments.id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="requested", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_refund_id: Mapped[str | None] = mapped_column(String(120), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(240))
    correlation_id: Mapped[str | None] = mapped_column(String(255))


class InboundWebhookEvent(UUIDMixin, Base):
    __tablename__ = "inbound_webhook_events"
    __table_args__ = (UniqueConstraint("provider", "provider_event_id", name="uq_inbound_provider_event"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="received", nullable=False)


class FailedJob(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "failed_jobs"
    __table_args__ = (Index("ix_failed_jobs_task_status", "task_name", "status"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("tenants.id"))
    task_name: Mapped[str] = mapped_column(String(160), nullable=False)
    queue_name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=20), default=JobStatus.FAILED, nullable=False
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    aggregate_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class OutboxEvent(UUIDMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_status_available", "status", "available_at"),
        Index("ix_outbox_tenant_event", "tenant_id", "event_type"),
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("tenants.id"))

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    queue: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class ProcessedEvent(Base):
    __tablename__ = "processed_events"

    consumer: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


@event.listens_for(User, "before_insert")
@event.listens_for(User, "before_update")
def validate_user_tenancy(mapper: Mapper[User], connection: Connection, target: User) -> None:
    target.validate_tenancy()
