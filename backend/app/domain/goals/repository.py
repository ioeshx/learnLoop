"""Learning goal persistence contract."""

from typing import Protocol

from app.domain.goals.models import LearningGoal


class LearningGoalRepository(Protocol):
    async def add(self, goal: LearningGoal) -> None: ...

    async def get(self, goal_id: str) -> LearningGoal | None: ...

    async def update(self, goal: LearningGoal) -> None: ...

    async def list_for_user(self, user_id: str) -> list[LearningGoal]: ...
