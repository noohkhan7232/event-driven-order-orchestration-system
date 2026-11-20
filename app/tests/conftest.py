import os
from collections.abc import Generator
from contextlib import nullcontext
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

test_url = make_url(os.environ.get("TEST_DATABASE_URL", "sqlite:///./test_order_processing.db"))
if test_url.get_backend_name() == "postgresql" and not (test_url.database or "").endswith("_test"):
    raise RuntimeError("Refusing destructive tests: PostgreSQL database name must end in _test")
os.environ.setdefault("DATABASE_URL", str(test_url.render_as_string(hide_password=False)))
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from app.auth.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import *  # noqa: F401,F403,E402
from app.models.entities import DEFAULT_TENANT_ID, Inventory, Order, OrderItem, Tenant, User, Warehouse  # noqa: E402
from app.models.enums import OrderStatus, UserRole  # noqa: E402
from app.workers import tasks  # noqa: E402

engine = (
    create_engine(test_url, connect_args={"check_same_thread": False})
    if test_url.get_backend_name() == "sqlite"
    else create_engine(test_url, pool_size=60, max_overflow=0)
)
TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


@pytest.fixture(autouse=True)
def reset_database(request):
    if request.node.path.name == "test_order_state.py":
        yield
        return
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestingSessionLocal.begin() as setup:
        setup.add(Tenant(id=DEFAULT_TENANT_ID, name="Default Store", slug="default"))
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db() -> Generator[Session, None, None]:
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db: Session):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def auth_header(client: TestClient, email: str, password: str, role: str = "customer") -> dict[str, str]:
    payload = {"email": email, "password": password, "full_name": "Test User", "role": role}
    if role == "customer":
        client.post("/api/v1/auth/signup", json=payload)
    else:
        with TestingSessionLocal() as session:
            session.add(
                User(
                    tenant_id=DEFAULT_TENANT_ID,
                    email=email,
                    full_name="Test User",
                    hashed_password=hash_password(password),
                    role=UserRole(role),
                )
            )
            session.commit()
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def workflow(db, monkeypatch):
    user = User(
        tenant_id=DEFAULT_TENANT_ID,
        email="flow@example.com",
        hashed_password="hash",
        full_name="Flow",
        role=UserRole.CUSTOMER,
    )
    warehouse = Warehouse(tenant_id=DEFAULT_TENANT_ID, code="FLOW", name="Flow", region="test")
    db.add_all([user, warehouse])
    db.flush()
    stock = Inventory(
        tenant_id=DEFAULT_TENANT_ID, sku="SKU", product_name="Product", warehouse_id=warehouse.id, available_quantity=10
    )
    order = Order(
        tenant_id=DEFAULT_TENANT_ID,
        principal_scope=f"user:{user.id}",
        customer_id=user.id,
        idempotency_key=str(uuid4()),
        status=OrderStatus.CREATED,
    )
    order.items.append(OrderItem(sku="SKU", product_name="Product", quantity=1, unit_price=10))
    db.add_all([stock, order])
    db.commit()
    monkeypatch.setattr(tasks, "SessionLocal", lambda: nullcontext(db))
    return order, stock


def pytest_collection_modifyitems(items):
    if engine.dialect.name != "postgresql":
        for item in items:
            if "postgres" in item.keywords:
                item.add_marker(
                    pytest.mark.skip(reason="Requires TEST_DATABASE_URL pointing to dedicated PostgreSQL *_test DB")
                )
