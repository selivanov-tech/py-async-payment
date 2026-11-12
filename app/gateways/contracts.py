import uuid
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import Currency, PaymentStatus


class PaymentChargeCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    payment_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: Currency
    idempotency_key: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=500)


class PaymentChargeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: PaymentStatus
    provider_payment_id: str | None = None
    failure_reason: str | None = None

    @field_validator("status")
    @classmethod
    def status_must_be_final(cls, value: PaymentStatus) -> PaymentStatus:
        if value == PaymentStatus.PENDING:
            raise ValueError("A gateway charge must return a final payment status")
        return value


@runtime_checkable
class PaymentGateway(Protocol):
    async def charge(self, command: PaymentChargeCommand) -> PaymentChargeResult: ...
