import asyncio
import logging

from faststream import FastStream
from faststream.exceptions import NackMessage
from faststream.rabbit import RabbitBroker

from app.composition import create_payment_gateway
from app.config import get_settings
from app.db import session_factory
from app.outbox import OutboxRelay
from app.schemas import PaymentCreatedEvent
from app.services.processing import PaymentProcessor
from app.topology import PAYMENTS_EXCHANGE, PAYMENTS_QUEUE
from app.webhooks import WebhookSender

logger = logging.getLogger(__name__)

settings = get_settings()
broker = RabbitBroker(settings.rabbitmq_url, fail_fast=False)
app = FastStream(broker)
gateway = create_payment_gateway(settings)
webhook = WebhookSender(settings.webhook_timeout)
processor = PaymentProcessor(gateway, webhook)
relay = OutboxRelay(broker, session_factory, settings)


@broker.subscriber(PAYMENTS_QUEUE, exchange=PAYMENTS_EXCHANGE)
async def handle_payment(event: PaymentCreatedEvent) -> None:
    for attempt in range(1, settings.max_attempts + 1):
        try:
            await processor.process(event)
            return
        except Exception as exc:
            if attempt == settings.max_attempts:
                logger.error(
                    "Payment message %s failed after %s attempts; sending to DLQ",
                    event.payment_id,
                    settings.max_attempts,
                )
                # RabbitMQ routes this rejected message through the queue's DLX.
                raise NackMessage(requeue=False) from exc
            delay = settings.retry_base_delay * (2 ** (attempt - 1))
            logger.warning(
                "Payment message %s attempt %s failed; retrying in %.2fs: %s",
                event.payment_id,
                attempt,
                delay,
                exc,
            )
            await asyncio.sleep(delay)


@app.on_startup
async def startup() -> None:
    await webhook.start()
    relay.start()


@app.on_shutdown
async def shutdown() -> None:
    await relay.stop()
    await webhook.stop()


if __name__ == "__main__":
    asyncio.run(app.run())
