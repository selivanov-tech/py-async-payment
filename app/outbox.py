import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime

from faststream.rabbit import RabbitBroker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.repositories.outbox import OutboxRepository
from app.schemas import PaymentCreatedEvent
from app.topology import (
    DEAD_LETTER_EXCHANGE,
    DEAD_LETTER_QUEUE,
    DEAD_LETTER_ROUTING_KEY,
    PAYMENTS_EXCHANGE,
    PAYMENTS_QUEUE,
    PAYMENTS_ROUTING_KEY,
)

logger = logging.getLogger(__name__)


async def declare_topology(broker: RabbitBroker) -> None:
    payments_exchange = await broker.declare_exchange(PAYMENTS_EXCHANGE)
    dead_letter_exchange = await broker.declare_exchange(DEAD_LETTER_EXCHANGE)

    payments_queue = await broker.declare_queue(PAYMENTS_QUEUE)
    await payments_queue.bind(payments_exchange, routing_key=PAYMENTS_ROUTING_KEY)

    dead_letter_queue = await broker.declare_queue(DEAD_LETTER_QUEUE)
    await dead_letter_queue.bind(dead_letter_exchange, routing_key=DEAD_LETTER_ROUTING_KEY)


class OutboxRelay:
    def __init__(
        self,
        broker: RabbitBroker,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self.broker = broker
        self.session_factory = session_factory
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._topology_ready = False

    async def publish_pending(self) -> int:
        async with self.session_factory() as session, session.begin():
            outbox = OutboxRepository(session)
            events = await outbox.list_unpublished(self.settings.outbox_batch_size)
            for event in events:
                payload = PaymentCreatedEvent.model_validate(event.payload)
                await self.broker.publish(
                    payload.model_dump(mode="json"),
                    exchange=PAYMENTS_EXCHANGE,
                    routing_key=PAYMENTS_ROUTING_KEY,
                    message_id=str(event.id),
                    headers={"event_type": event.event_type},
                    mandatory=True,
                    persist=True,
                )
                event.published_at = datetime.now(UTC)
            return len(events)

    async def run(self) -> None:
        while True:
            try:
                if not self.broker.running:
                    await asyncio.sleep(self.settings.outbox_poll_interval)
                    continue
                if not self._topology_ready:
                    await declare_topology(self.broker)
                    self._topology_ready = True
                await self.publish_pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Outbox publishing failed; unpublished rows will be retried")
            await asyncio.sleep(self.settings.outbox_poll_interval)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="outbox-relay")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
