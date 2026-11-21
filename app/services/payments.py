from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import AuditLog, Order, Payment
from app.models.enums import PaymentStatus
from app.providers.contracts import ProviderDeclined
from app.providers.registry import get_payment_provider
from app.services.refund_intents import request_refund


class PaymentService:
    def __init__(self, db: Session):
        self.db = db

    def authorize(self, order: Order, *, retry_attempt: int = 0) -> Payment:
        provider = get_payment_provider()
        attempt = (
            self.db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.entity_id == order.id, AuditLog.action == "order_reopened")
            )
            or 0
        )
        payment = Payment(order_id=order.id, amount=Decimal(order.total_amount), status=PaymentStatus.INITIATED)
        payment.provider = provider.name
        try:
            result = provider.authorize(
                order, idempotency_key=f"{order.id}:authorize:{attempt}", retry_attempt=retry_attempt
            )
        except ProviderDeclined:
            payment.status = PaymentStatus.FAILED
            payment.failure_code = "SIMULATED_DECLINE"
        else:
            payment.status = result.status
            payment.provider_reference = result.provider_reference
            payment.provider_transaction_id = result.provider_reference
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

    def refund(self, order: Order, *, reason: str = "ORDER_CANCELLED", correlation_id: str | None = None) -> None:
        for payment in order.payments:
            if payment.provider == "cod":
                continue
            if payment.status in {PaymentStatus.AUTHORIZED, PaymentStatus.CAPTURED}:
                request_refund(self.db, order, payment, reason, correlation_id=correlation_id)
            elif payment.status == PaymentStatus.INITIATED:
                payment.status, payment.provider_status = PaymentStatus.FAILED, "abandoned"
