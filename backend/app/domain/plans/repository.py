"""Study plan persistence contract."""

from typing import Protocol

from app.domain.plans.models import StudyPlan


class StudyPlanRepository(Protocol):
    async def add(self, plan: StudyPlan) -> None: ...

    async def get(self, plan_id: str) -> StudyPlan | None: ...

    async def list_for_goal(self, goal_id: str) -> list[StudyPlan]: ...
