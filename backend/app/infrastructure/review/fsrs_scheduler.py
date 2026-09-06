"""Py-FSRS implementation of the review scheduler contract."""

from datetime import datetime

from fsrs import Card, Rating, Scheduler

from app.domain.common import require_aware_utc
from app.domain.review.models import ReviewRating, ReviewSchedule, ScheduledReview


class FsrsReviewScheduler:
    def __init__(self, scheduler: Scheduler | None = None) -> None:
        self._scheduler = scheduler or Scheduler(enable_fuzzing=False)

    def review(
        self,
        *,
        user_id: str,
        knowledge_node_id: str,
        rating: ReviewRating,
        reviewed_at: datetime,
        current: ReviewSchedule | None = None,
    ) -> ScheduledReview:
        reviewed_at = require_aware_utc(reviewed_at, "reviewed_at")
        if current is not None and (
            current.user_id != user_id or current.knowledge_node_id != knowledge_node_id
        ):
            raise ValueError("current review schedule belongs to another card")
        card = (
            Card(due=reviewed_at)
            if current is None
            else Card.from_json(current.card_json)
        )
        updated_card, review_log = self._scheduler.review_card(
            card,
            Rating(rating.value),
            review_datetime=reviewed_at,
        )
        return ScheduledReview(
            schedule=ReviewSchedule(
                user_id=user_id,
                knowledge_node_id=knowledge_node_id,
                card_json=updated_card.to_json(),
                due_at=updated_card.due,
                last_review_at=updated_card.last_review,
            ),
            review_log_json=review_log.to_json(),
        )
