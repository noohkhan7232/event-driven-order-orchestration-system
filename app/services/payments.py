from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.entities import AuditLog, Order, Payment
from app.models.enums import PaymentStatus


class PaymentService:
    def __init__(self, db: Session):
        self.db = db

    def authorize(self, order: Order, simulate_failure: bool = False) -> Payment:
        payment = Payment(order_id=order.id, amount=Decimal(order.total_amount), status=PaymentStatus.INITIATED)
        if simulate_failure:
            payment.status = PaymentStatus.FAILED
            payment.failure_code = "SIMULATED_DECLINE"
        else:
            payment.status = PaymentStatus.AUTHORIZED
            payment.provider_transaction_id = f"txn_{uuid4().hex[:18]}"
        self.db.add(payment)
        self.db.add(
            AuditLog(
                entity_type="payment",
                entity_id=order.id,
                action=f"payment_{payment.status.value}",
                payload={"provider": payment.provider},
            )
        )
        return payment

    def refund(self, order: Order) -> None:
        for payment in order.payments:
            if payment.status == PaymentStatus.AUTHORIZED:
                payment.status = PaymentStatus.REFUNDED
                self.db.add(AuditLog(entity_type="payment", entity_id=payment.id, action="payment_refunded", payload={}))
