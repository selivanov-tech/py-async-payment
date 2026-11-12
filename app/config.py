from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://payments:payments@localhost:15432/payments"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5673/"
    api_key: str = "dev-secret-key"
    payment_gateway_backend: str = "mock"

    outbox_poll_interval: float = 0.5
    outbox_batch_size: int = 100
    gateway_min_delay: float = 2.0
    gateway_max_delay: float = 5.0
    webhook_timeout: float = 5.0
    retry_base_delay: float = 1.0
    max_attempts: int = 3

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
