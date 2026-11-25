from uuid import uuid4

import pytest

from app.auth.security import create_access_token
from app.models.entities import OrderTimeline, Payment, Refund, Shipment, ShipmentEvent, Tenant, User
from app.models.enums import PaymentStatus, ShipmentStatus, UserRole


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.VENDOR, UserRole.CUSTOMER])
def test_dashboard_activity_is_scoped_and_projects_only_safe_history(client, db, workflow, role):
    order, _ = workflow
    staff = User(
        tenant_id=order.tenant_id, email="activity@test.example", full_name="Staff", hashed_password="unused", role=role
    )
    db.add(staff)
    db.add(
        OrderTimeline(tenant_id=order.tenant_id, order_id=order.id, to_status="created", reason="PRIVATE_DIAGNOSTIC")
    )
    payment = Payment(
        order_id=order.id,
        status=PaymentStatus.CAPTURED,
        amount=10,
        provider_status="captured",
        checkout_payload={"secret": "DO_NOT_EXPOSE"},
    )
    shipment = Shipment(order_id=order.id)
    db.add_all([payment, shipment])
    db.flush()
    db.add(ShipmentEvent(shipment_id=shipment.id, status=ShipmentStatus.CREATED, notes="PRIVATE_NOTES"))
    db.add(
        Refund(
            tenant_id=order.tenant_id,
            order_id=order.id,
            payment_id=payment.id,
            amount=10,
            currency="INR",
            reason="cancelled",
            status="processing",
            idempotency_key=str(uuid4()),
            last_error="PRIVATE_ERROR",
        )
    )
    db.commit()
    path = f"/api/v1/orders/{order.id}/activity"
    assert client.get(path).status_code == 401
    auth = {"Authorization": "Bearer " + create_access_token(str(staff.id))}
    result = client.get(path, headers=auth)
    assert result.status_code == (404 if role == UserRole.CUSTOMER else 200)
    if role != UserRole.CUSTOMER:
        data = result.json()
        assert data["timeline"][0]["status"] == "created"
        assert data["refunds"][0]["status"] == "processing"
        assert data["payments"][0]["provider_status"] == "captured"
        assert data["shipments"][0]["status"] == "created"
        assert set(data) == {"timeline", "payments", "shipments", "refunds"}
        for secret in [
            "PRIVATE_DIAGNOSTIC",
            "DO_NOT_EXPOSE",
            "PRIVATE_NOTES",
            "PRIVATE_ERROR",
            "idempotency_key",
            "checkout_payload",
        ]:
            assert secret not in result.text
    foreign = Tenant(name="Other", slug="other-activity")
    db.add(foreign)
    db.flush()
    staff.tenant_id = foreign.id
    db.commit()
    assert client.get(path, headers=auth).status_code == 404
