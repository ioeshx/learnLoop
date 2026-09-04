"""Review schedule entities independent of the FSRS library."""

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum

from app.domain.common import require_aware_utc


class ReviewRating(IntEnum):
    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4


@dataclass(frozen=True, slots=True)
class ReviewSchedule:
    user_id: str
    knowledge_node_id: str
    card_json: str
    due_at: datetime
    last_review_at: datetime | None

    def __post_init__(self) -> None:
        require_aware_utc(self.due_at, "due_at")
        if self.last_review_at is not None:
            require_aware_utc(self.last_review_at, "last_review_at")


@dataclass(frozen=True, slots=True)
class ScheduledReview:
    schedule: ReviewSchedule
    review_log_json: str
