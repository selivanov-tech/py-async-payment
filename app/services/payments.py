import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutboxEvent, Payment, PaymentStatus, uuid7
from app.repositories.outbox import OutboxRepository
from app.repositories.payments import PaymentRepository
from app.schemas import PaymentCreate


class IdempotencyConflictError(Exception):
    """The idempotency key belongs to a request with a different payload."""


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._payments = PaymentRepository(session)
        self._outbox = OutboxRepository(session)

    async def create(self, body: PaymentCreate, idempotency_key: str) -> Payment:
        webhook_url = str(body.webhook_url)
        try:
            async with self._session.begin():
                payment = Payment(
                    id=uuid7(),
                    amount=body.amount,
                    currency=body.currency,
                    description=body.description,
                    metadata_=body.metadata,
                    status=PaymentStatus.PENDING,
                    idempotency_key=idempotency_key,
                    webhook_url=webhook_url,
                )
                self._payments.add(payment)
                await self._session.flush()

                event_id = uuid7()
                self._outbox.add(
                    OutboxEvent(
                        id=event_id,
                        payment_id=payment.id,
                        event_type="payment.created",
                        payload={"event_id": str(event_id), "payment_id": str(payment.id)},
                    )
                )
                await self._session.flush()
                return payment
        except IntegrityError as exc:
            # A concurrent duplicate rolls back both inserts. Return the original
            # only when the complete request payload is identical.
            payment = await self._payments.get_by_idempotency_key(idempotency_key)
            if payment is None:
                raise exc
            if not self._same_request(payment, body, webhook_url):
                raise IdempotencyConflictError from exc
            return payment

    async def get(self, payment_id: uuid.UUID) -> Payment | None:
        return await self._payments.get_by_id(payment_id)

    @staticmethod
    def _same_request(payment: Payment, body: PaymentCreate, webhook_url: str) -> bool:
        return (
            payment.amount == body.amount
            and payment.currency == body.currency
            and payment.description == body.description
            and payment.metadata_ == body.metadata
            and payment.webhook_url == webhook_url
        )
