import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
from faststream.exceptions import NackMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.consumer as consumer_module
from app.gateways.contracts import PaymentChargeCommand, PaymentChargeResult
from app.models import Currency, Payment, PaymentStatus, uuid7
from app.schemas import PaymentCreatedEvent, PaymentWebhookPayload
from app.services.processing import PaymentProcessor
from app.webhooks import WebhookSender


class FakeGateway:
    def __init__(self, result: PaymentStatus = PaymentStatus.SUCCEEDED) -> None:
        self.result = result
        self.calls = 0
        self.command: PaymentChargeCommand | None = None

    async def charge(self, command: PaymentChargeCommand) -> PaymentChargeResult:
        self.calls += 1
        self.command = command
        return PaymentChargeResult(
            status=self.result,
            provider_payment_id=f"fake-{command.payment_id}",
        )


class FakeWebhook(WebhookSender):
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls = 0
        self.url: str | None = None
        self.payload: PaymentWebhookPayload | None = None

    async def send(self, webhook_url: str, payload: PaymentWebhookPayload) -> None:
        self.calls += 1
        self.url = webhook_url
        self.payload = payload
        if self.calls <= self.failures:
            raise RuntimeError("Webhook unavailable")


async def create_payment(factory: async_sessionmaker[AsyncSession]) -> Payment:
    async with factory() as session, session.begin():
        payment = Payment(
            id=uuid7(),
            amount="25.50",
            currency="USD",
            description="Consumer test",
            metadata_={"source": "test"},
            status=PaymentStatus.PENDING,
            idempotency_key=f"consumer-{uuid7()}",
            webhook_url="https://client.example/webhook",
        )
        session.add(payment)
        await session.flush()
        return payment


async def test_webhook_retry_does_not_charge_gateway_twice(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payment = await create_payment(session_factory)
    event = PaymentCreatedEvent(event_id=uuid7(), payment_id=payment.id)
    gateway = FakeGateway()
    webhook = FakeWebhook(failures=1)
    processor = PaymentProcessor(gateway, webhook, session_factory)

    with pytest.raises(RuntimeError, match="Webhook unavailable"):
        await processor.process(event)
    await processor.process(event)

    assert gateway.calls == 1
    assert gateway.command is not None
    assert gateway.command.idempotency_key == payment.idempotency_key
    assert webhook.calls == 2
    assert webhook.payload is not None
    assert webhook.payload.payment_id == payment.id
    async with session_factory() as session:
        stored = await session.get(Payment, payment.id)
        assert stored.status == PaymentStatus.SUCCEEDED
        assert stored.processed_at is not None


async def test_failed_gateway_result_is_a_final_payment_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payment = await create_payment(session_factory)
    gateway = FakeGateway(PaymentStatus.FAILED)
    webhook = FakeWebhook()
    processor = PaymentProcessor(gateway, webhook, session_factory)

    await processor.process(PaymentCreatedEvent(event_id=uuid7(), payment_id=payment.id))

    async with session_factory() as session:
        stored = await session.get(Payment, payment.id)
        assert stored.status == PaymentStatus.FAILED
        assert stored.processed_at is not None
    assert webhook.calls == 1
    assert webhook.payload is not None
    assert webhook.payload.status == PaymentStatus.FAILED


async def test_webhook_sender_serializes_pydantic_payload() -> None:
    captured: dict = {}

    def receive(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(204)

    sender = WebhookSender(timeout=5)
    sender.client = httpx.AsyncClient(transport=httpx.MockTransport(receive))
    payload = PaymentWebhookPayload(
        payment_id=uuid7(),
        status=PaymentStatus.SUCCEEDED,
        amount=Decimal("25.50"),
        currency=Currency.USD,
        description="Consumer test",
        metadata={"source": "test"},
        created_at=datetime(2026, 11, 12, 10, 0, tzinfo=UTC),
        processed_at=datetime(2026, 11, 12, 10, 0, 3, tzinfo=UTC),
    )

    try:
        await sender.send("https://client.example/webhook", payload)
    finally:
        await sender.stop()

    assert captured["url"] == "https://client.example/webhook"
    assert captured["body"] == payload.model_dump(mode="json")
    assert captured["body"]["amount"] == "25.50"


async def test_consumer_nacks_to_dlq_after_three_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing_processor = AsyncMock()
    failing_processor.process.side_effect = RuntimeError("down")
    monkeypatch.setattr(consumer_module, "processor", failing_processor)
    monkeypatch.setattr(consumer_module.settings, "max_attempts", 3)
    monkeypatch.setattr(consumer_module.settings, "retry_base_delay", 1)
    sleep = AsyncMock()
    monkeypatch.setattr(consumer_module.asyncio, "sleep", sleep)

    event = PaymentCreatedEvent(event_id=uuid7(), payment_id=uuid7())
    with pytest.raises(NackMessage) as exc_info:
        await consumer_module.handle_payment(event)

    assert exc_info.value.extra_options == {"requeue": False}
    assert failing_processor.process.await_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [1, 2]
