from decimal import Decimal

import pytest

from app.composition import create_payment_gateway
from app.config import Settings
from app.gateways.contracts import PaymentChargeCommand, PaymentChargeResult
from app.gateways.mock import MockPaymentGateway
from app.models import Currency, PaymentStatus, uuid7


@pytest.fixture
def charge_command() -> PaymentChargeCommand:
    return PaymentChargeCommand(
        payment_id=uuid7(),
        amount=Decimal("1499.90"),
        currency=Currency.RUB,
        idempotency_key="order-42",
        description="Order 42",
    )


async def test_mock_gateway_returns_success_contract(
    monkeypatch: pytest.MonkeyPatch,
    charge_command: PaymentChargeCommand,
) -> None:
    monkeypatch.setattr("app.gateways.mock.random.uniform", lambda _start, _end: 0)
    monkeypatch.setattr("app.gateways.mock.random.random", lambda: 0.5)
    gateway = MockPaymentGateway(min_delay=0, max_delay=0)

    result = await gateway.charge(charge_command)

    assert result.status == PaymentStatus.SUCCEEDED
    assert result.provider_payment_id == f"mock-{charge_command.payment_id}"
    assert result.failure_reason is None


async def test_mock_gateway_returns_business_failure(
    monkeypatch: pytest.MonkeyPatch,
    charge_command: PaymentChargeCommand,
) -> None:
    monkeypatch.setattr("app.gateways.mock.random.uniform", lambda _start, _end: 0)
    monkeypatch.setattr("app.gateways.mock.random.random", lambda: 0.95)
    gateway = MockPaymentGateway(min_delay=0, max_delay=0)

    result = await gateway.charge(charge_command)

    assert result.status == PaymentStatus.FAILED
    assert result.failure_reason == "Mock provider declined the payment"


def test_gateway_result_rejects_pending_status() -> None:
    with pytest.raises(ValueError, match="final payment status"):
        PaymentChargeResult(status=PaymentStatus.PENDING)


def test_composition_root_selects_mock_gateway() -> None:
    gateway = create_payment_gateway(Settings(payment_gateway_backend="mock"))

    assert isinstance(gateway, MockPaymentGateway)


def test_composition_root_rejects_unknown_gateway() -> None:
    with pytest.raises(ValueError, match="Unsupported payment gateway backend"):
        create_payment_gateway(Settings(payment_gateway_backend="unknown"))
