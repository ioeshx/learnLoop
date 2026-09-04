"""Mastery event and snapshot entities."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.common import new_id, require_aware_utc, utc_now


class MasteryEventType(StrEnum):
    CORRECT_FIRST_TRY = "correct_first_try"
    CORRECT_AFTER_REMEDIATION = "correct_after_remediation"
    INCORRECT = "incorrect"
    REVIEW_CORRECT = "review_correct"
    USER_CORRECTION = "user_correction"


@dataclass(frozen=True, slots=True)
class MasteryEvent:
    id: str
    user_id: str
    knowledge_node_id: str
    event_type: MasteryEventType
    delta: float
    occurred_at: datetime
    attempt_id: str | None = None

    def __post_init__(self) -> None:
        if not -1.0 <= self.delta <= 1.0:
            raise ValueError("mastery delta must be between -1.0 and 1.0")
        require_aware_utc(self.occurred_at, "occurred_at")

    @classmethod
    def create(
        cls,
        *,
        user_id: str,
        knowledge_node_id: str,
        event_type: MasteryEventType,
        delta: float,
        attempt_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> "MasteryEvent":
        return cls(
            id=new_id(),
            user_id=user_id,
            knowledge_node_id=knowledge_node_id,
            event_type=event_type,
            delta=delta,
            attempt_id=attempt_id,
            occurred_at=require_aware_utc(occurred_at or utc_now(), "occurred_at"),
        )


@dataclass(frozen=True, slots=True)
class MasterySnapshot:
    user_id: str
    knowledge_node_id: str
    score: float
    attempt_count: int
    correct_count: int
    updated_at: datetime

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("mastery score must be between 0.0 and 1.0")
        if self.attempt_count < 0 or self.correct_count < 0:
            raise ValueError("mastery counters must not be negative")
        if self.correct_count > self.attempt_count:
            raise ValueError("correct_count cannot exceed attempt_count")
        require_aware_utc(self.updated_at, "updated_at")
