"""Local Docker fixtures scoped to an operations-* synthetic tenant; no credential output."""

import json
import sys
from uuid import UUID, uuid4

from sqlalchemy import select

from app.auth.security import hash_password
from app.db.session import SessionLocal
from app.events.contracts import DomainEvent, EventNames
from app.models.entities import (
    FailedJob,
    Order,
    OrderItem,
    Product,
    Tenant,
    TenantPaymentConfig,
    User,
    WebhookEndpoint,
)
from app.models.enums import JobStatus, OrderStatus, UserRole

payload = json.load(sys.stdin)
with SessionLocal() as db:
    tenant = db.get(Tenant, UUID(payload["tenant_id"]))
    if tenant is None or not tenant.slug.startswith("operations-"):
        raise RuntimeError("Fixture requires an operations-* test tenant")
    if payload["kind"] == "roles":
        suffix = tenant.slug.removeprefix("operations-")
        users = []
        for role in ("vendor", "root"):
            row = User(
                tenant_id=tenant.id if role == "vendor" else None,
                email=f"operations-{role}-{suffix}@example.com",
                full_name="Operations fixture",
                hashed_password=hash_password(payload["password"]),
                role=UserRole.VENDOR if role == "vendor" else UserRole.ADMIN,
                is_platform_admin=role == "root",
            )
            db.add(row)
            db.flush()
            users.append({"id": str(row.id), "email": row.email})
        db.commit()
        print(json.dumps({"vendor": users[0], "root": users[1]}))
    elif payload["kind"] == "jobs":
        owner = db.get(User, UUID(payload["owner_id"]))
        product = db.scalar(select(Product).where(Product.tenant_id == tenant.id, Product.sku == payload["sku"]))
        if owner is None or owner.tenant_id != tenant.id or product is None:
            raise RuntimeError("Fixture owner/product mismatch")
        jobs = []
        for _ in range(2):
            order = Order(
                tenant_id=tenant.id,
                principal_scope=f"user:{owner.id}",
                customer_id=owner.id,
                idempotency_key=str(uuid4()),
                status=OrderStatus.FAILED,
                failure_reason="RETRIES_EXHAUSTED",
                currency=product.currency,
                total_amount=product.price,
            )
            order.items.append(
                OrderItem(sku=product.sku, product_name=product.name, quantity=1, unit_price=product.price)
            )
            db.add(order)
            db.flush()
            event = DomainEvent(
                event_type=EventNames.ORDER_CREATED,
                tenant_id=tenant.id,
                aggregate_id=order.id,
                payload={"order_id": str(order.id)},
            )
            job = FailedJob(
                tenant_id=tenant.id,
                aggregate_id=order.id,
                event_id=event.event_id,
                task_name="app.workers.tasks.process_event",
                queue_name="orders",
                status=JobStatus.FAILED,
                error_message="RETRIES_EXHAUSTED",
                retry_count=3,
                payload=event.model_dump(mode="json"),
            )
            db.add(job)
            db.flush()
            jobs.append({"id": str(job.id), "order_id": str(order.id)})
        db.commit()
        print(json.dumps(jobs))
    elif payload["kind"] == "cleanup":
        tenant.status = "suspended"
        for row in db.scalars(select(User).where(User.tenant_id == tenant.id)):
            row.is_active = False
        root = db.get(User, UUID(payload["root_id"])) if payload.get("root_id") else None
        if root and root.email == f"operations-root-{tenant.slug.removeprefix('operations-')}@example.com":
            root.is_active = False
        for row in db.scalars(select(TenantPaymentConfig).where(TenantPaymentConfig.tenant_id == tenant.id)):
            row.is_active = False
        for row in db.scalars(select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == tenant.id)):
            row.is_active = False
        db.commit()
        print(json.dumps({"cleaned": True}))
    else:
        raise RuntimeError("Unknown fixture kind")
