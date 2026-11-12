import httpx

from app.schemas import PaymentWebhookPayload


class WebhookSender:
    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self.client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=self.timeout)

    async def stop(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    async def send(self, webhook_url: str, payload: PaymentWebhookPayload) -> None:
        if self.client is None:
            raise RuntimeError("Webhook sender is not started")
        response = await self.client.post(
            webhook_url,
            json=payload.model_dump(mode="json"),
        )
        response.raise_for_status()
