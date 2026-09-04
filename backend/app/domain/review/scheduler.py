"""Spaced-repetition scheduler contract."""

from datetime import datetime
from typing import Protocol

from app.domain.review.models import ReviewRating, ReviewSchedule, ScheduledReview


class ReviewScheduler(Protocol):
    def review(
        self,
        *,
        user_id: str,
        knowledge_node_id: str,
        rating: ReviewRating,
        reviewed_at: datetime,
        current: ReviewSchedule | None = None,
    ) -> ScheduledReview: ...
