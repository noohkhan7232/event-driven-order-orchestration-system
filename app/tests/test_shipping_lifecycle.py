from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError
from test_consumers import consume
from test_payment_config import identity

from app.core.config import Settings
from app.events.contracts import EventNames
from app.models.entities import AuditLog, OutboxEvent, Shipment, ShipmentEvent, Tenant
from app.models.enums import OrderStatus, ShipmentStatus
from app.workers import tasks


def drain_shipping(db):
    for row in db.query(OutboxEvent).filter_by(event_type="shipment.status_changed").all():
        tasks.process_event.run(row.payload)
    for row in db.query(OutboxEvent).filter_by(event_type="order.status_changed").all():
        tasks.process_event.run(row.payload)


@pytest.fixture
def manual_order(db, workflow, client):
    order, stock = workflow
    _, auth = identity(db)
    response = client.patch("/api/v1/settings/shipping", headers=auth, json={"shipping_provider": "manual"})
    assert response.status_code == 200, response.text
    return order, stock, auth


@pytest.mark.parametrize("method", ["prepaid", "cod"])
def test_manual_api_lifecycle_is_async_monotone_and_collects_cod_once(db, client, manual_order, method):
    order, _, auth = manual_order
    order.payment_method = method
    db.commit()
    for kind in (EventNames.ORDER_CREATED, EventNames.PAYMENT_REQUESTED, EventNames.PAYMENT_AUTHORIZED):
        consume(order, kind)
    shipment = db.query(Shipment).one()
    assert shipment.provider == "manual" and shipment.status == ShipmentStatus.CREATED
    assert shipment.tracking_number is None
    tracking = {
        "carrier": "Seller Courier",
        "tracking_number": "TRACK-123",
        "tracking_url": "https://example.com/track/123",
        "estimated_delivery": "2026-12-01",
    }
    response = client.patch(f"/api/v1/shipments/{shipment.id}", headers=auth, json=tracking)
    assert response.status_code == 200, response.text
    result = client.get(f"/api/v1/orders/{order.id}", headers=auth).json()["shipments"][0]
    assert all(result[key] == value for key, value in tracking.items())
    for status in ("delayed", "in_transit", "in_transit", "delivered", "created", "in_transit", "delivered"):
        before = order.status
        assert (
            client.post(
                f"/api/v1/shipments/{shipment.id}/events",
                headers=auth,
                json={"status": status, "notes": "private operator note"},
            ).status_code
            == 200
        )
        assert order.status == before
        drain_shipping(db)
        if status == "delayed":
            assert order.status == OrderStatus.FULFILLMENT_REQUESTED
    assert order.status == OrderStatus.DELIVERED and shipment.status == ShipmentStatus.DELIVERED
    assert db.query(ShipmentEvent).count() == 8
    assert db.query(OutboxEvent).filter_by(event_type="shipment.status_changed").count() == 7
    assert all("private operator" not in str(row.payload) for row in db.query(OutboxEvent))
    assert db.query(AuditLog).filter_by(action="cod_collected").count() == int(method == "cod")
    if method == "cod":
        assert order.payment_status == "collected"


@pytest.mark.parametrize("delivery_first", [False, True])
def test_cancelled_order_ignores_delivery_and_preserves_cod_stock(db, client, manual_order, delivery_first):
    order, stock, auth = manual_order
    order.payment_method = "cod"
    db.commit()
    consume(order, EventNames.ORDER_CREATED)
    consume(order, EventNames.PAYMENT_REQUESTED)
    shipment = db.query(Shipment).one()
    if delivery_first:
        assert (
            client.post(
                f"/api/v1/shipments/{shipment.id}/events", headers=auth, json={"status": "delivered"}
            ).status_code
            == 200
        )
    assert client.post(f"/api/v1/orders/{order.id}/cancel", headers=auth).status_code == 200
    assert (
        client.post(f"/api/v1/shipments/{shipment.id}/events", headers=auth, json={"status": "delivered"}).status_code
        == 200
    )
    drain_shipping(db)
    assert order.status == OrderStatus.CANCELLED and shipment.status == ShipmentStatus.CANCELLED
    assert (stock.available_quantity, stock.reserved_quantity) == (10, 0)
    assert db.query(AuditLog).filter_by(action="cod_collected").count() == 0


@pytest.mark.parametrize(
    "fields",
    [
        {"tracking_url": "javascript:alert(1)"},
        {"tracking_url": "http://example.com"},
        {"tracking_url": "https://user:pass@example.com"},
        {"carrier": "x" * 61},
        {"tracking_number": "x" * 81},
    ],
)
def test_tracking_validation_and_foreign_ownership(db, client, manual_order, fields):
    order, _, auth = manual_order
    consume(order, EventNames.ORDER_CREATED)
    consume(order, EventNames.PAYMENT_REQUESTED)
    consume(order, EventNames.PAYMENT_AUTHORIZED)
    shipment = db.query(Shipment).one()
    assert client.patch(f"/api/v1/shipments/{shipment.id}", headers=auth, json=fields).status_code == 422
    foreign = Tenant(name="Foreign", slug=uuid4().hex)
    db.add(foreign)
    db.commit()
    _, other_auth = identity(db, tenant_id=foreign.id)
    assert (
        client.patch(f"/api/v1/shipments/{shipment.id}", headers=other_auth, json={"carrier": "other"}).status_code
        == 404
    )


def test_manual_provider_and_nonlocal_mock_guard():
    from app.providers.manual import ManualShippingProvider

    assert ManualShippingProvider().name == "manual"
    with pytest.raises(ValidationError, match="shipping"):
        Settings(
            _env_file=None,
            environment="production",
            secret_key="x" * 32,
            allow_mock_providers=True,
            credentials_encryption_key=Fernet.generate_key().decode(),
            shipping_provider="mock",
        )


def test_delivered_before_transit_bridges_states_once_and_stale_ids_do_not_crash(db, client, manual_order):
    from app.events.contracts import DomainEvent
    from app.models.entities import OrderTimeline

    order, _, auth = manual_order
    order.payment_method = "cod"
    db.commit()
    consume(order, EventNames.ORDER_CREATED)
    consume(order, EventNames.PAYMENT_REQUESTED)
    shipment = db.query(Shipment).one()
    assert (
        client.post(f"/api/v1/shipments/{shipment.id}/events", headers=auth, json={"status": "delivered"}).status_code
        == 200
    )
    drain_shipping(db)
    drain_shipping(db)
    assert order.status == OrderStatus.DELIVERED and order.payment_status == "collected"
    assert db.query(OrderTimeline).filter_by(to_status="shipped").count() == 1
    assert db.query(OrderTimeline).filter_by(to_status="delivered").count() == 1
    for identity_value in ("bad", str(uuid4())):
        tasks.process_event.run(
            DomainEvent(
                event_type="shipment.status_changed",
                aggregate_id=order.id,
                tenant_id=order.tenant_id,
                payload={"shipment_event_id": identity_value},
            ).model_dump(mode="json")
        )
    assert db.query(AuditLog).filter_by(action="cod_collected").count() == 1


def test_shipping_permissions_configuration_and_tracking_conflict(db, client, manual_order):
    from app.auth.principal import Principal
    from app.auth.security import create_access_token
    from app.schemas.tenancy import ApiKeyCreate
    from app.services.tenancy import create_api_key

    order, _, auth = manual_order
    assert client.get("/api/v1/settings/shipping", headers=auth).json() == {"shipping_provider": "manual"}
    consume(order, EventNames.ORDER_CREATED)
    consume(order, EventNames.PAYMENT_REQUESTED)
    consume(order, EventNames.PAYMENT_AUTHORIZED)
    shipment = db.query(Shipment).one()
    owner, _ = identity(db)
    _, token = create_api_key(
        db, Principal(owner.tenant_id, user=owner), ApiKeyCreate(name="Shipping", scopes=["orders:write"])
    )
    key_auth = {"X-API-Key": token}
    assert (
        client.patch(f"/api/v1/shipments/{shipment.id}", headers=key_auth, json={"carrier": "Key Courier"}).status_code
        == 200
    )
    assert client.get("/api/v1/settings/shipping", headers=key_auth).status_code == 403
    assert (
        client.patch("/api/v1/settings/shipping", headers=key_auth, json={"shipping_provider": "mock"}).status_code
        == 403
    )
    customer = {"Authorization": "Bearer " + create_access_token(str(order.customer_id))}
    assert client.patch(f"/api/v1/shipments/{shipment.id}", headers=customer, json={"carrier": "No"}).status_code == 403
    assert client.patch(f"/api/v1/shipments/{shipment.id}", headers=auth, json={"carrier": None}).status_code == 400
    second = Shipment(order_id=order.id, tracking_number="TAKEN")
    db.add(second)
    db.commit()
    assert (
        client.patch(f"/api/v1/shipments/{shipment.id}", headers=auth, json={"tracking_number": "TAKEN"}).status_code
        == 409
    )
    assert shipment.tracking_number is None
    tenant = db.get(Tenant, order.tenant_id)
    tenant.status = "suspended"
    db.commit()
    assert client.patch(f"/api/v1/shipments/{shipment.id}", headers=auth, json={"carrier": "No"}).status_code == 403
