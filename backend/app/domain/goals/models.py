"""Learning goal entities."""

from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum

from app.domain.common import new_id, require_aware_utc, require_text, utc_now


class GoalStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class LearningGoal:
    id: str
    user_id: str
    title: str
    description: str
    desired_outcome: str
    weekly_minutes: int
    status: GoalStatus
    created_at: datetime
    updated_at: datetime
    target_date: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", require_text(self.title, "title"))
        object.__setattr__(
            self,
            "desired_outcome",
            require_text(self.desired_outcome, "desired_outcome"),
        )
        if self.weekly_minutes <= 0:
            raise ValueError("weekly_minutes must be positive")
        require_aware_utc(self.created_at, "created_at")
        require_aware_utc(self.updated_at, "updated_at")

    @classmethod
    def create(
        cls,
        *,
        user_id: str,
        title: str,
        desired_outcome: str,
        weekly_minutes: int,
        description: str = "",
        target_date: date | None = None,
        now: datetime | None = None,
        goal_id: str | None = None,
    ) -> "LearningGoal":
        created_at = require_aware_utc(now or utc_now(), "now")
        return cls(
            id=goal_id or new_id(),
            user_id=require_text(user_id, "user_id"),
            title=title,
            description=description.strip(),
            desired_outcome=desired_outcome,
            weekly_minutes=weekly_minutes,
            target_date=target_date,
            status=GoalStatus.DRAFT,
            created_at=created_at,
            updated_at=created_at,
        )

    def revise(
        self,
        *,
        title: str | None = None,
        description: str | None = None,
        desired_outcome: str | None = None,
        weekly_minutes: int | None = None,
        target_date: date | None = None,
        now: datetime | None = None,
    ) -> "LearningGoal":
        return replace(
            self,
            title=self.title if title is None else title,
            description=self.description if description is None else description,
            desired_outcome=(
                self.desired_outcome if desired_outcome is None else desired_outcome
            ),
            weekly_minutes=(
                self.weekly_minutes if weekly_minutes is None else weekly_minutes
            ),
            target_date=self.target_date if target_date is None else target_date,
            updated_at=require_aware_utc(now or utc_now(), "now"),
        )

    def change_status(
        self, status: GoalStatus, *, now: datetime | None = None
    ) -> "LearningGoal":
        return replace(
            self,
            status=status,
            updated_at=require_aware_utc(now or utc_now(), "now"),
        )
