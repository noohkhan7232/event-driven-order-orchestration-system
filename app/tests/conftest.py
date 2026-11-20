import os
from collections.abc import Generator
from contextlib import nullcontext
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_order_processing.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from app.auth.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import *  # noqa: F401,F403,E402
from app.models.entities import Inventory, Order, OrderItem, User, Warehouse  # noqa: E402
from app.models.enums import OrderStatus, UserRole  # noqa: E402
from app.workers import tasks  # noqa: E402

engine = create_engine("sqlite:///./test_order_processing.db", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


@pytest.fixture(autouse=True)
def reset_database(request):
    if request.node.path.name == "test_order_state.py":
        yield
        return
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
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
                User(email=email, full_name="Test User", hashed_password=hash_password(password), role=UserRole(role))
            )
            session.commit()
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def workflow(db, monkeypatch):
    user = User(email="flow@example.com", hashed_password="hash", full_name="Flow", role=UserRole.CUSTOMER)
    warehouse = Warehouse(code="FLOW", name="Flow", region="test")
    db.add_all([user, warehouse])
    db.flush()
    stock = Inventory(sku="SKU", product_name="Product", warehouse_id=warehouse.id, available_quantity=10)
    order = Order(customer_id=user.id, idempotency_key=str(uuid4()), status=OrderStatus.CREATED)
    order.items.append(OrderItem(sku="SKU", product_name="Product", quantity=1, unit_price=10))
    db.add_all([stock, order])
    db.commit()
    monkeypatch.setattr(tasks, "SessionLocal", lambda: nullcontext(db))
    return order, stock
