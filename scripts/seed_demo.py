from app.auth.security import hash_password
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.entities import DEFAULT_TENANT_ID, Inventory, Tenant, User, Warehouse
from app.models.enums import UserRole


def main() -> None:
    with SessionLocal() as db:
        if db.get(Tenant, DEFAULT_TENANT_ID) is None:
            db.add(Tenant(id=DEFAULT_TENANT_ID, name="Default Store", slug="default"))
            db.flush()
        if not db.query(User).filter_by(email="admin@orders.local").first():
            db.add(
                User(
                    tenant_id=DEFAULT_TENANT_ID,
                    email="admin@orders.local",
                    full_name="Platform Admin",
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
        warehouse = db.query(Warehouse).filter_by(code="DFW-01").first()
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
        print("Seeded demo users, warehouse, and inventory.")


if __name__ == "__main__":
    main()
