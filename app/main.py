from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from faststream.rabbit import RabbitBroker

from app.api import router
from app.config import get_settings
from app.db import session_factory
from app.outbox import OutboxRelay
from app.security import verify_api_key


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    broker = RabbitBroker(settings.rabbitmq_url)
    await broker.start()
    relay = OutboxRelay(broker, session_factory, settings)
    relay.start()
    try:
        yield
    finally:
        await relay.stop()
        await broker.close()


app = FastAPI(
    title="Async Payment Service",
    version="0.1.0",
    description="Transactional Outbox, asynchronous processing and webhook notifications.",
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/health", tags=["service"], dependencies=[Depends(verify_api_key)])
async def health() -> dict[str, str]:
    return {"status": "ok"}
