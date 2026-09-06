"""Tests for the py-fsrs adapter."""

from datetime import UTC, datetime

from app.domain.review import ReviewRating
from app.infrastructure.review import FsrsReviewScheduler

NOW = datetime(2026, 1, 10, 8, 30, tzinfo=UTC)


def test_fsrs_creates_and_advances_a_review_schedule() -> None:
    scheduler = FsrsReviewScheduler()

    first = scheduler.review(
        user_id="user-1",
        knowledge_node_id="node-1",
        rating=ReviewRating.GOOD,
        reviewed_at=NOW,
    )
    second = scheduler.review(
        user_id="user-1",
        knowledge_node_id="node-1",
        rating=ReviewRating.GOOD,
        reviewed_at=first.schedule.due_at,
        current=first.schedule,
    )

    assert first.schedule.due_at > NOW
    assert second.schedule.due_at > first.schedule.due_at
    assert second.schedule.last_review_at == first.schedule.due_at
    assert first.review_log_json
