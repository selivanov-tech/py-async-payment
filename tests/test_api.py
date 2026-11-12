import uuid

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import OutboxEvent, Payment, uuid7


async def test_create_payment_writes_payment_and_outbox_atomically(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    payment_body: dict,
) -> None:
    response = await client.post(
        "/api/v1/payments",
        json=payment_body,
        headers={"Idempotency-Key": "order-42"},
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "pending"
    assert uuid.UUID(data["payment_id"]).version == 7
    assert data["created_at"]

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Payment)) == 1
        assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
        event = await session.scalar(select(OutboxEvent))
        assert event.payload["payment_id"] == data["payment_id"]
        assert event.published_at is None


async def test_idempotent_replay_returns_original_without_new_outbox_event(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    payment_body: dict,
) -> None:
    headers = {"Idempotency-Key": "same-key"}
    first = await client.post("/api/v1/payments", json=payment_body, headers=headers)
    second = await client.post("/api/v1/payments", json=payment_body, headers=headers)

    assert first.status_code == second.status_code == 202
    assert second.json() == first.json()
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Payment)) == 1
        assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1


async def test_idempotency_key_with_different_payload_is_rejected(
    client: httpx.AsyncClient,
    payment_body: dict,
) -> None:
    headers = {"Idempotency-Key": "same-key"}
    first = await client.post("/api/v1/payments", json=payment_body, headers=headers)
    changed_body = {**payment_body, "amount": "1500.00"}
    second = await client.post("/api/v1/payments", json=changed_body, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 409


async def test_get_payment_and_not_found(
    client: httpx.AsyncClient,
    payment_body: dict,
) -> None:
    created = await client.post(
        "/api/v1/payments",
        json=payment_body,
        headers={"Idempotency-Key": "get-payment"},
    )
    payment_id = created.json()["payment_id"]

    response = await client.get(f"/api/v1/payments/{payment_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == payment_id
    assert data["amount"] == "1499.90"
    assert data["metadata"] == {"order_id": 42}
    assert data["processed_at"] is None

    missing = await client.get(f"/api/v1/payments/{uuid7()}")
    assert missing.status_code == 404


async def test_api_key_is_required_for_all_endpoints(
    client: httpx.AsyncClient,
    payment_body: dict,
) -> None:
    assert (await client.get("/health", headers={"X-API-Key": "wrong"})).status_code == 401
    assert (await client.get("/health", headers={"X-API-Key": ""})).status_code == 401
    response = await client.post(
        "/api/v1/payments",
        json=payment_body,
        headers={"X-API-Key": "wrong", "Idempotency-Key": "unauthorized"},
    )
    assert response.status_code == 401


async def test_invalid_payment_payload_is_rejected(
    client: httpx.AsyncClient,
    payment_body: dict,
) -> None:
    response = await client.post(
        "/api/v1/payments",
        json={**payment_body, "amount": "0"},
        headers={"Idempotency-Key": "invalid-amount"},
    )
    assert response.status_code == 422


async def test_idempotency_key_is_required(
    client: httpx.AsyncClient,
    payment_body: dict,
) -> None:
    response = await client.post("/api/v1/payments", json=payment_body)

    assert response.status_code == 422
