"""Study session entity and lifecycle rules."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from app.domain.common import new_id, require_aware_utc, require_text, utc_now


class StudySessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class StudySession:
    id: str
    goal_id: str
    plan_item_id: str
    status: StudySessionStatus
    started_at: datetime
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        require_text(self.id, "id")
        require_text(self.goal_id, "goal_id")
        require_text(self.plan_item_id, "plan_item_id")
        require_aware_utc(self.started_at, "started_at")
        if self.completed_at is not None:
            require_aware_utc(self.completed_at, "completed_at")
        if self.status == StudySessionStatus.ACTIVE and self.completed_at is not None:
            raise ValueError("an active study session cannot have completed_at")
        if self.status == StudySessionStatus.COMPLETED and self.completed_at is None:
            raise ValueError("a completed study session requires completed_at")

    @classmethod
    def create(
        cls,
        *,
        goal_id: str,
        plan_item_id: str,
        session_id: str | None = None,
        now: datetime | None = None,
    ) -> "StudySession":
        return cls(
            id=session_id or new_id(),
            goal_id=goal_id,
            plan_item_id=plan_item_id,
            status=StudySessionStatus.ACTIVE,
            started_at=require_aware_utc(now or utc_now(), "now"),
        )

    def complete(self, *, now: datetime | None = None) -> "StudySession":
        if self.status == StudySessionStatus.COMPLETED:
            return self
        return replace(
            self,
            status=StudySessionStatus.COMPLETED,
            completed_at=require_aware_utc(now or utc_now(), "now"),
        )
