"""Study session entity and lifecycle rules."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from app.domain.common import new_id, require_aware_utc, require_text, utc_now


class StudySessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"


class StudySessionKind(StrEnum):
    """区分正常学习会话和不应推进计划进度的独立复习会话。"""

    LEARNING = "learning"
    REVIEW = "review"


@dataclass(frozen=True, slots=True)
class StudySession:
    """一次学习或复习活动的不可变领域实体。

    `kind` 决定完成会话时是否推进学习计划，`exercise_id` 允许复习和补救流程绑定动态
    生成的题目；创建与完成方法通过返回新实例维护生命周期不变量。
    """

    id: str
    goal_id: str
    plan_item_id: str
    status: StudySessionStatus
    started_at: datetime
    completed_at: datetime | None = None
    kind: StudySessionKind = StudySessionKind.LEARNING
    exercise_id: str | None = None

    def __post_init__(self) -> None:
        require_text(self.id, "id")
        require_text(self.goal_id, "goal_id")
        require_text(self.plan_item_id, "plan_item_id")
        if self.exercise_id is not None:
            require_text(self.exercise_id, "exercise_id")
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
        kind: StudySessionKind = StudySessionKind.LEARNING,
        exercise_id: str | None = None,
    ) -> "StudySession":
        return cls(
            id=session_id or new_id(),
            goal_id=goal_id,
            plan_item_id=plan_item_id,
            status=StudySessionStatus.ACTIVE,
            started_at=require_aware_utc(now or utc_now(), "now"),
            kind=kind,
            exercise_id=exercise_id,
        )

    def complete(self, *, now: datetime | None = None) -> "StudySession":
        if self.status == StudySessionStatus.COMPLETED:
            return self
        return replace(
            self,
            status=StudySessionStatus.COMPLETED,
            completed_at=require_aware_utc(now or utc_now(), "now"),
        )
