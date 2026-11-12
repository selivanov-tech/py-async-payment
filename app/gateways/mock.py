import asyncio
import random

from app.gateways.contracts import PaymentChargeCommand, PaymentChargeResult
from app.models import PaymentStatus


class MockPaymentGateway:
    """Deterministic contract with a real provider, random delays and outcomes for local use."""

    def __init__(self, min_delay: float, max_delay: float) -> None:
        self.min_delay = min_delay
        self.max_delay = max_delay

    async def charge(self, command: PaymentChargeCommand) -> PaymentChargeResult:
        await asyncio.sleep(random.uniform(self.min_delay, self.max_delay))
        succeeded = random.random() < 0.9
        return PaymentChargeResult(
            status=PaymentStatus.SUCCEEDED if succeeded else PaymentStatus.FAILED,
            provider_payment_id=f"mock-{command.payment_id}",
            failure_reason=None if succeeded else "Mock provider declined the payment",
        )
