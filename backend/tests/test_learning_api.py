"""HTTP contract test for the deterministic learning loop."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.application import (
    ApplicationDependencies,
    Curriculum,
    CurriculumGenerationError,
)
from app.config import Settings
from app.domain.goals import LearningGoal
from app.infrastructure.review import FsrsReviewScheduler
from app.main import create_app
from tests.fakes import FakeUnitOfWorkFactory


class RejectingCurriculumGenerator:
    async def generate(self, goal: LearningGoal, *, now: datetime) -> Curriculum:
        del goal, now
        raise CurriculumGenerationError("generated graph contains a cycle")


@pytest.mark.asyncio
async def test_learning_api_completes_the_vertical_slice(
    test_settings: Settings,
) -> None:
    factory = FakeUnitOfWorkFactory()
    dependencies = ApplicationDependencies(
        uow_factory=factory,
        review_scheduler=FsrsReviewScheduler(),
    )

    application = create_app(test_settings)
    application.state.application_dependencies = dependencies
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        goal_response = await client.post(
            "/api/v1/goals",
            headers={"Idempotency-Key": "api-goal-1"},
            json={
                "title": "Learn graph algorithms",
                "description": "A deterministic test goal",
                "desired_outcome": "Implement BFS independently",
                "weekly_minutes": 180,
            },
        )
        assert goal_response.status_code == 201
        goal = goal_response.json()

        restored_goal = await client.get(f"/api/v1/goals/{goal['id']}")
        assert restored_goal.status_code == 200
        assert restored_goal.json() == goal

        plan_response = await client.post(f"/api/v1/goals/{goal['id']}/plans")
        assert plan_response.status_code == 201
        plan = plan_response.json()
        assert len(plan["items"]) == 3

        restored_plan = await client.get(f"/api/v1/plans/{plan['id']}")
        assert restored_plan.status_code == 200
        assert restored_plan.json() == plan

        session_response = await client.post(
            "/api/v1/study-sessions",
            headers={"Idempotency-Key": "api-session-1"},
            json={
                "goal_id": goal["id"],
                "plan_item_id": plan["items"][0]["id"],
            },
        )
        assert session_response.status_code == 201
        session = session_response.json()
        assert session["latest_result"] is None
        assert session["lesson_content"]

        attempt_response = await client.post(
            f"/api/v1/study-sessions/{session['id']}/attempts",
            headers={"Idempotency-Key": "api-attempt-1"},
            json={
                "exercise_id": session["exercise"]["id"],
                "selected_options": [session["exercise"]["options"][0]],
            },
        )
        assert attempt_response.status_code == 201
        attempt = attempt_response.json()
        assert attempt["is_correct"] is True
        assert attempt["mastery_score"] == 0.15

        restored_session = (
            await client.get(f"/api/v1/study-sessions/{session['id']}")
        ).json()
        assert restored_session["latest_result"]["attempt_id"] == attempt["attempt_id"]

        complete_response = await client.post(
            f"/api/v1/study-sessions/{session['id']}/complete"
        )
        assert complete_response.status_code == 200
        assert complete_response.json()["status"] == "completed"

        due_response = await client.get(
            "/api/v1/reviews/due", params={"due_before": attempt["due_at"]}
        )
        assert due_response.status_code == 200
        assert len(due_response.json()) == 1


@pytest.mark.asyncio
async def test_mutating_endpoint_requires_idempotency_key(
    test_settings: Settings,
) -> None:
    application = create_app(test_settings)
    application.state.application_dependencies = ApplicationDependencies(
        uow_factory=FakeUnitOfWorkFactory(),
        review_scheduler=FsrsReviewScheduler(),
    )
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/goals",
            json={
                "title": "Learn SQL",
                "desired_outcome": "Write queries",
                "weekly_minutes": 60,
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_invalid_generated_curriculum_returns_stable_api_error(
    test_settings: Settings,
) -> None:
    factory = FakeUnitOfWorkFactory()
    application = create_app(test_settings)
    application.state.application_dependencies = ApplicationDependencies(
        uow_factory=factory,
        review_scheduler=FsrsReviewScheduler(),
        curriculum_generator=RejectingCurriculumGenerator(),
    )
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        goal_response = await client.post(
            "/api/v1/goals",
            headers={"Idempotency-Key": "failed-curriculum-goal"},
            json={
                "title": "Learn SQL",
                "desired_outcome": "Write queries",
                "weekly_minutes": 60,
            },
        )
        goal_id = goal_response.json()["id"]
        response = await client.post(f"/api/v1/goals/{goal_id}/plans")

    assert response.status_code == 502
    assert response.json()["error"] == {
        "code": "curriculum_generation_failed",
        "message": "generated graph contains a cycle",
        "details": {},
    }
    assert factory.state.plans == {}
    assert factory.state.nodes == {}


@pytest.mark.asyncio
async def test_adaptive_review_api_starts_corrects_and_defers_review(
    test_settings: Settings,
) -> None:
    clock = [datetime(2026, 4, 1, 9, tzinfo=UTC)]
    factory = FakeUnitOfWorkFactory()
    application = create_app(test_settings)
    application.state.application_dependencies = ApplicationDependencies(
        uow_factory=factory,
        review_scheduler=FsrsReviewScheduler(),
        clock=lambda: clock[0],
    )
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        goal = (
            await client.post(
                "/api/v1/goals",
                headers={"Idempotency-Key": "review-api-goal"},
                json={
                    "title": "图算法",
                    "desired_outcome": "独立实现 BFS",
                    "weekly_minutes": 180,
                },
            )
        ).json()
        plan = (await client.post(f"/api/v1/goals/{goal['id']}/plans")).json()
        learning = (
            await client.post(
                "/api/v1/study-sessions",
                headers={"Idempotency-Key": "review-api-learning"},
                json={
                    "goal_id": goal["id"],
                    "plan_item_id": plan["items"][0]["id"],
                },
            )
        ).json()
        attempt = (
            await client.post(
                f"/api/v1/study-sessions/{learning['id']}/attempts",
                headers={"Idempotency-Key": "review-api-first-attempt"},
                json={
                    "exercise_id": learning["exercise"]["id"],
                    "selected_options": [learning["exercise"]["options"][0]],
                },
            )
        ).json()
        await client.post(f"/api/v1/study-sessions/{learning['id']}/complete")
        clock[0] = datetime.fromisoformat(attempt["due_at"]) + timedelta(minutes=1)

        due_response = await client.get("/api/v1/reviews/due")
        assert due_response.status_code == 200
        due = due_response.json()[0]
        assert due["priority_score"] > 0
        assert due["reason"]

        review_response = await client.post(
            "/api/v1/reviews/sessions",
            headers={"Idempotency-Key": "review-api-session"},
            json={"knowledge_node_id": due["knowledge_node_id"]},
        )
        assert review_response.status_code == 201
        review = review_response.json()
        assert review["kind"] == "review"
        assert review["adaptation"]["reasons"]

        review_attempt = (
            await client.post(
                f"/api/v1/study-sessions/{review['id']}/attempts",
                headers={"Idempotency-Key": "review-api-attempt"},
                json={
                    "exercise_id": review["exercise"]["id"],
                    "selected_options": [review["exercise"]["options"][1]],
                },
            )
        ).json()
        corrected = await client.patch(
            f"/api/v1/study-sessions/{review['id']}/attempts/"
            f"{review_attempt['attempt_id']}",
            json={"selected_options": [review["exercise"]["options"][0]]},
        )
        assert corrected.status_code == 200
        assert corrected.json()["is_correct"] is True

        deferred = await client.post(
            f"/api/v1/reviews/{due['knowledge_node_id']}/defer",
            json={"days": 2},
        )
        assert deferred.status_code == 200
        assert datetime.fromisoformat(deferred.json()["due_at"]) == (
            clock[0] + timedelta(days=2)
        )
