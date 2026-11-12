from fastapi import Depends, FastAPI

from app.api import router
from app.security import verify_api_key

app = FastAPI(
    title="Async Payment Service",
    version="0.1.0",
    description="Transactional Outbox, asynchronous processing and webhook notifications.",
)
app.include_router(router)


@app.get("/health", tags=["service"], dependencies=[Depends(verify_api_key)])
async def health() -> dict[str, str]:
    return {"status": "ok"}
