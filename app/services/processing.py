from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import session_factory
from app.gateways.contracts import PaymentChargeCommand, PaymentGateway
from app.models import PaymentStatus
from app.repositories.payments import PaymentRepository
from app.schemas import PaymentCreatedEvent, PaymentWebhookPayload
from app.webhooks import WebhookSender


class PaymentProcessor:
    def __init__(
        self,
        gateway: PaymentGateway,
        webhook: WebhookSender,
        factory: async_sessionmaker[AsyncSession] = session_factory,
    ) -> None:
        self.gateway = gateway
        self.webhook = webhook
        self.session_factory = factory

    async def process(self, event: PaymentCreatedEvent) -> None:
        async with self.session_factory() as session:
            payments = PaymentRepository(session)
            payment = await payments.get_by_id(event.payment_id)
            if payment is None:
                raise ValueError(f"Payment {event.payment_id} does not exist")

            if payment.status == PaymentStatus.PENDING:
                result = await self.gateway.charge(
                    PaymentChargeCommand(
                        payment_id=payment.id,
                        amount=payment.amount,
                        currency=payment.currency,
                        idempotency_key=payment.idempotency_key,
                        description=payment.description,
                    )
                )
                payment.status = result.status
                payment.processed_at = datetime.now(UTC)
                await session.commit()

            # A retry after a webhook failure reuses the final payment state:
            # the gateway is never called twice for the same payment.
            payload = PaymentWebhookPayload(
                payment_id=payment.id,
                status=payment.status,
                amount=payment.amount,
                currency=payment.currency,
                description=payment.description,
                metadata=payment.metadata_,
                created_at=payment.created_at,
                processed_at=payment.processed_at,
            )
            await self.webhook.send(payment.webhook_url, payload)
