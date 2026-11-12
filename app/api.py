import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Payment
from app.schemas import PaymentCreate, PaymentCreated, PaymentRead
from app.security import verify_api_key
from app.services.payments import IdempotencyConflictError, PaymentService

router = APIRouter(
    prefix="/api/v1/payments",
    tags=["payments"],
    dependencies=[Depends(verify_api_key)],
)

type SessionDependency = Annotated[AsyncSession, Depends(get_session)]


def get_payment_service(session: SessionDependency) -> PaymentService:
    return PaymentService(session)


type ServiceDependency = Annotated[PaymentService, Depends(get_payment_service)]


@router.post("", response_model=PaymentCreated, status_code=status.HTTP_202_ACCEPTED)
async def create_payment(
    body: PaymentCreate,
    service: ServiceDependency,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> PaymentCreated:
    try:
        payment = await service.create(body, idempotency_key)
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key was already used with a different payload",
        ) from exc

    return PaymentCreated(
        payment_id=payment.id,
        status=payment.status,
        created_at=payment.created_at,
    )


@router.get("/{payment_id}", response_model=PaymentRead)
async def get_payment(payment_id: uuid.UUID, service: ServiceDependency) -> Payment:
    payment = await service.get(payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return payment
