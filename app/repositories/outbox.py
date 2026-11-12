from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutboxEvent


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, event: OutboxEvent) -> None:
        self._session.add(event)

    async def list_unpublished(self, limit: int) -> Sequence[OutboxEvent]:
        return list(
            await self._session.scalars(
                select(OutboxEvent)
                .where(OutboxEvent.published_at.is_(None))
                .order_by(OutboxEvent.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
