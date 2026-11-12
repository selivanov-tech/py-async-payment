import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import OutboxEvent, Payment, PaymentStatus, uuid7
from app.outbox import OutboxRelay


class FakeBroker:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.messages = []

    async def publish(self, message, **kwargs) -> None:
        if self.fail:
            raise RuntimeError("RabbitMQ unavailable")
        self.messages.append((message, kwargs))


async def create_outbox_event(factory: async_sessionmaker[AsyncSession]) -> OutboxEvent:
    async with factory() as session, session.begin():
        payment = Payment(
            id=uuid7(),
            amount="10.00",
            currency="RUB",
            description="Outbox test",
            metadata_={},
            status=PaymentStatus.PENDING,
            idempotency_key=f"outbox-{uuid7()}",
            webhook_url="https://client.example/webhook",
        )
        event_id = uuid7()
        event = OutboxEvent(
            id=event_id,
            payment_id=payment.id,
            event_type="payment.created",
            payload={"event_id": str(event_id), "payment_id": str(payment.id)},
        )
        session.add_all([payment, event])
        await session.flush()
        return event


async def test_outbox_relay_marks_event_only_after_confirmed_publish(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await create_outbox_event(session_factory)
    broker = FakeBroker()
    relay = OutboxRelay(broker, session_factory, Settings(outbox_batch_size=10))

    assert await relay.publish_pending() == 1
    assert len(broker.messages) == 1
    message, kwargs = broker.messages[0]
    assert message["payment_id"] == str(event.payment_id)
    assert kwargs["message_id"] == str(event.id)
    assert kwargs["persist"] is True
    assert kwargs["mandatory"] is True

    async with session_factory() as session:
        stored = await session.get(OutboxEvent, event.id)
        assert stored.published_at is not None


async def test_outbox_event_stays_unpublished_when_broker_fails(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await create_outbox_event(session_factory)
    relay = OutboxRelay(FakeBroker(fail=True), session_factory, Settings())

    with pytest.raises(RuntimeError, match="RabbitMQ unavailable"):
        await relay.publish_pending()

    async with session_factory() as session:
        stored = await session.get(OutboxEvent, event.id)
        assert stored.published_at is None
        pending = list(
            await session.scalars(select(OutboxEvent).where(OutboxEvent.published_at.is_(None)))
        )
        assert len(pending) == 1
