from app.auth.security import hash_password
from app.db.session import SessionLocal
from app.models.entities import Inventory, User, Warehouse
from app.models.enums import UserRole


def main() -> None:
    with SessionLocal() as db:
        if not db.query(User).filter_by(email="admin@orders.local").first():
            db.add(
                User(
                    email="admin@orders.local",
                    full_name="Platform Admin",
                    hashed_password=hash_password("AdminPassword123"),
                    role=UserRole.ADMIN,
                )
            )
        if not db.query(User).filter_by(email="customer@orders.local").first():
            db.add(
                User(
                    email="customer@orders.local",
                    full_name="Demo Customer",
                    hashed_password=hash_password("CustomerPass123"),
                    role=UserRole.CUSTOMER,
                )
            )
        warehouse = db.query(Warehouse).filter_by(code="DFW-01").first()
        if not warehouse:
            warehouse = Warehouse(code="DFW-01", name="Dallas Fulfillment Center", region="south")
            db.add(warehouse)
            db.flush()
        for sku, name, quantity in [
            ("SKU-HEADPHONES", "Noise Cancelling Headphones", 250),
            ("SKU-KEYBOARD", "Mechanical Keyboard", 160),
            ("SKU-DOCK", "USB-C Docking Station", 75),
        ]:
            if not db.query(Inventory).filter_by(sku=sku, warehouse_id=warehouse.id).first():
                db.add(
                    Inventory(
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
