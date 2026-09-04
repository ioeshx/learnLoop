"""Review schedule persistence contract."""

from datetime import datetime
from typing import Protocol

from app.domain.review.models import ReviewSchedule


class ReviewRepository(Protocol):
    async def get(
        self, user_id: str, knowledge_node_id: str
    ) -> ReviewSchedule | None: ...

    async def save(self, schedule: ReviewSchedule) -> None: ...

    async def list_due(
        self, user_id: str, due_before: datetime
    ) -> list[ReviewSchedule]: ...
