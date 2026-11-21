from app.auth.principal import SCOPES, Principal
from app.auth.security import hash_password
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.entities import DEFAULT_TENANT_ID, ApiKey, Inventory, Product, Tenant, User, Warehouse
from app.models.enums import UserRole
from app.schemas.tenancy import ApiKeyCreate
from app.services.tenancy import create_api_key


def main() -> None:
    if get_settings().environment not in {"local", "development", "test"}:
        raise RuntimeError("Demo seeding is local only")
    with SessionLocal() as db:
        if db.get(Tenant, DEFAULT_TENANT_ID) is None:
            db.add(Tenant(id=DEFAULT_TENANT_ID, name="Default Store", slug="default"))
            db.flush()
        if not db.query(User).filter_by(email="admin@orders.local").first():
            db.add(
                User(
                    tenant_id=DEFAULT_TENANT_ID,
                    email="admin@orders.local",
                    full_name="Tenant Owner",
                    hashed_password=hash_password("AdminPassword123"),
                    role=UserRole.ADMIN,
                )
            )
        if not db.query(User).filter_by(email="customer@orders.local").first():
            db.add(
                User(
                    tenant_id=DEFAULT_TENANT_ID,
                    email="customer@orders.local",
                    full_name="Demo Customer",
                    hashed_password=hash_password("CustomerPass123"),
                    role=UserRole.CUSTOMER,
                )
            )
        if not db.query(User).filter_by(email="vendor@orders.local").first():
            db.add(
                User(
                    tenant_id=DEFAULT_TENANT_ID,
                    email="vendor@orders.local",
                    full_name="Demo Vendor",
                    hashed_password=hash_password("VendorPassword123"),
                    role=UserRole.VENDOR,
                )
            )
        if not db.query(User).filter_by(email="platform@orders.local").first():
            db.add(
                User(
                    email="platform@orders.local",
                    full_name="Platform Admin",
                    hashed_password=hash_password("PlatformPassword123"),
                    role=UserRole.ADMIN,
                    is_platform_admin=True,
                )
            )
        warehouse = db.query(Warehouse).filter_by(tenant_id=DEFAULT_TENANT_ID, code="DFW-01").first()
        if not warehouse:
            warehouse = Warehouse(
                tenant_id=DEFAULT_TENANT_ID, code="DFW-01", name="Dallas Fulfillment Center", region="south"
            )
            db.add(warehouse)
            db.flush()
        products = [
            ("SKU-HEADPHONES", "Noise Cancelling Headphones", 250),
            ("SKU-KEYBOARD", "Mechanical Keyboard", 160),
            ("SKU-DOCK", "USB-C Docking Station", 75),
        ]
        if get_settings().failure_injection_enabled:
            products += [
                (sku, "Failure injection demo", 100)
                for sku in [
                    "FAIL-PAYMENT-DEMO",
                    "FAIL-SHIPMENT-DEMO",
                    "FAIL-TRANSIENT-DEMO",
                    "FAIL-TRANSIENT-EXHAUST-DEMO",
                ]
            ]
        for sku, name, quantity in products:
            if not db.query(Product).filter_by(tenant_id=DEFAULT_TENANT_ID, sku=sku).first():
                db.add(Product(tenant_id=DEFAULT_TENANT_ID, sku=sku, name=name, price="49.99", currency="INR"))
            if not db.query(Inventory).filter_by(sku=sku, warehouse_id=warehouse.id).first():
                db.add(
                    Inventory(
                        tenant_id=DEFAULT_TENANT_ID,
                        sku=sku,
                        product_name=name,
                        warehouse_id=warehouse.id,
                        available_quantity=quantity,
                        low_stock_threshold=25,
                    )
                )
        db.commit()
        owner = (
            db.query(User)
            .filter_by(email="admin@orders.local", tenant_id=DEFAULT_TENANT_ID, role=UserRole.ADMIN)
            .first()
        )
        if owner and not db.query(ApiKey).filter_by(tenant_id=DEFAULT_TENANT_ID, name="Local demo integration").first():
            _, plaintext = create_api_key(
                db,
                Principal(DEFAULT_TENANT_ID, user=owner),
                ApiKeyCreate.model_validate({"name": "Local demo integration", "scopes": sorted(SCOPES)}),
            )
            print(f"Demo API key (shown once): {plaintext}")
        print("Seeded demo users, warehouse, and inventory.")


if __name__ == "__main__":
    main()
