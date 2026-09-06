"""HTTP contract test for the deterministic learning loop."""

import httpx
import pytest

from app.application import ApplicationDependencies
from app.config import Settings
from app.infrastructure.review import FsrsReviewScheduler
from app.main import create_app
from tests.fakes import FakeUnitOfWorkFactory


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
