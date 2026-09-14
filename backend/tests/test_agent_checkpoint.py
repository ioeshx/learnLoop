"""Restart, interrupt/resume, and SSE tests for durable Agent execution."""

import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import httpx
import pytest

from app.agent.execution import AgentEvent, open_agent_runtime
from app.application import (
    ApplicationDependencies,
    CreateGoalCommand,
    CreateLearningGoal,
    CreateStudyPlan,
    StartSessionCommand,
    StartStudySession,
)
from app.config import Settings
from app.infrastructure.review import FsrsReviewScheduler
from app.main import create_app
from tests.fakes import FakeUnitOfWorkFactory

NOW = datetime(2026, 3, 3, 9, tzinfo=UTC)


async def _study_setup() -> tuple[
    FakeUnitOfWorkFactory,
    ApplicationDependencies,
    str,
    str,
]:
    factory = FakeUnitOfWorkFactory()
    dependencies = ApplicationDependencies(
        uow_factory=factory,
        review_scheduler=FsrsReviewScheduler(),
        clock=lambda: NOW,
    )
    goal = await CreateLearningGoal(dependencies).execute(
        CreateGoalCommand(
            title="图遍历",
            desired_outcome="独立实现 BFS",
            weekly_minutes=180,
            idempotency_key="checkpoint-goal",
        )
    )
    plan = await CreateStudyPlan(dependencies).execute(goal.id)
    session = await StartStudySession(dependencies).execute(
        StartSessionCommand(
            goal_id=goal.id,
            plan_item_id=plan.plan.items[0].id,
            idempotency_key="checkpoint-session",
        )
    )
    return (
        factory,
        dependencies,
        session.session.id,
        session.exercise.answer_key[0],
    )


async def _collect(events: object) -> list[AgentEvent]:
    collected: list[AgentEvent] = []
    async for event in events:  # type: ignore[attr-defined]
        collected.append(event)
    return collected


@pytest.mark.asyncio
async def test_checkpoint_survives_restart_and_resumes_same_thread(
    tmp_path: Path,
) -> None:
    factory, dependencies, session_id, correct_answer = await _study_setup()
    settings = Settings(environment="test", data_dir=tmp_path / "data")

    async with open_agent_runtime(settings, dependencies, None) as first_runtime:
        run, created = await first_runtime.create_run(
            "daily_learning", session_id
        )
        first_events = await _collect(first_runtime.execute(run))
        first_thread_id = run.thread_id
        assert created is True
        assert first_events[-1].event == "interrupt_created"
        assert first_events[-1].data["value"]["type"] == "answer_required"  # type: ignore[index]
        assert (await first_runtime.run_store.get(run.run_id)).status == (  # type: ignore[union-attr]
            "awaiting_input"
        )

    assert settings.checkpoint_path.exists()

    async with open_agent_runtime(settings, dependencies, None) as second_runtime:
        restored = await second_runtime.run_store.get(run.run_id)
        assert restored is not None
        assert restored.thread_id == first_thread_id
        answer_events = await _collect(
            second_runtime.execute(
                restored,
                resume={"selected_options": [correct_answer]},
            )
        )
        assert answer_events[-1].event == "interrupt_created"
        assert answer_events[-1].data["value"]["type"] == (  # type: ignore[index]
            "grade_review_required"
        )

        restored = await second_runtime.run_store.get(run.run_id)
        assert restored is not None
        final_events = await _collect(
            second_runtime.execute(restored, resume={"action": "accept"})
        )
        assert final_events[-1].event == "run_completed"
        state = await second_runtime.get_checkpoint_state(restored)
        assert state["values"]["status"] == "completed"  # type: ignore[index]
        tool_calls = await second_runtime.run_store.list_tool_calls(run.run_id)
        assert tool_calls
        assert all(call.status == "succeeded" for call in tool_calls)
        assert all(call.duration_ms is not None for call in tool_calls)

    assert len(factory.state.attempts) == 1


@pytest.mark.asyncio
async def test_agent_sse_stream_replays_and_resumes(
    tmp_path: Path,
) -> None:
    _, dependencies, session_id, correct_answer = await _study_setup()
    settings = Settings(environment="test", data_dir=tmp_path / "data")

    async with open_agent_runtime(settings, dependencies, None) as runtime:
        application = create_app(settings)
        application.state.application_dependencies = dependencies
        application.state.agent_runtime = runtime
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            started = await client.post(
                f"/api/v1/agent/study-sessions/{session_id}/runs"
            )
            assert started.status_code == 200
            assert started.headers["content-type"].startswith(
                "text/event-stream"
            )
            run_id = started.headers["x-agent-run-id"]
            first_events = _parse_sse(started.text)
            assert first_events[0]["event"] == "run_started"
            assert first_events[-1]["event"] == "interrupt_created"
            assert any(
                event["event"] == "tool_completed" for event in first_events
            )
            first_last_id = int(first_events[-1]["sequence"])

            run_response = await client.get(f"/api/v1/agent/runs/{run_id}")
            assert run_response.json()["status"] == "awaiting_input"

            answered = await client.post(
                f"/api/v1/agent/runs/{run_id}/resume",
                json={"value": {"selected_options": [correct_answer]}},
            )
            answer_events = _parse_sse(answered.text)
            assert answer_events[-1]["data"]["value"]["type"] == (  # type: ignore[index]
                "grade_review_required"
            )

            replayed = await client.get(
                f"/api/v1/agent/runs/{run_id}/events",
                headers={"Last-Event-ID": str(first_last_id)},
            )
            replayed_events = _parse_sse(replayed.text)
            assert replayed_events
            assert all(
                int(event["sequence"]) > first_last_id
                for event in replayed_events
            )

            state = await client.get(f"/api/v1/agent/runs/{run_id}/state")
            assert state.json()["next"] == ["review_grade"]

            completed = await client.post(
                f"/api/v1/agent/runs/{run_id}/resume",
                json={"value": {"action": "accept"}},
            )
            assert _parse_sse(completed.text)[-1]["event"] == "run_completed"

            runs = await client.get("/api/v1/agent/runs")
            assert runs.status_code == 200
            assert runs.json()[0]["run_id"] == run_id

            trace = await client.get(f"/api/v1/agent/runs/{run_id}/trace")
            assert trace.status_code == 200
            assert trace.json()["tool_calls"]
            assert trace.json()["total_tool_duration_ms"] >= 0
            assert trace.json()["delegations"] == []
            assert trace.json()["child_runs"] == []

            private_trace = await client.get(
                f"/api/v1/agent/runs/{run_id}/trace?include_context=true"
            )
            assert private_trace.status_code == 403

            duplicate = await client.post(
                f"/api/v1/agent/runs/{run_id}/resume",
                json={"value": {"action": "accept"}},
            )
            assert duplicate.status_code == 409

            deleted = await client.delete(f"/api/v1/agent/runs/{run_id}")
            assert deleted.status_code == 204
            missing = await client.get(f"/api/v1/agent/runs/{run_id}")
            assert missing.status_code == 404


@pytest.mark.asyncio
async def test_cleanup_removes_only_expired_terminal_runs(tmp_path: Path) -> None:
    _, dependencies, _, _ = await _study_setup()
    settings = Settings(
        environment="test",
        data_dir=tmp_path / "data",
        checkpoint_retention_days=1,
    )

    async with open_agent_runtime(settings, dependencies, None) as runtime:
        completed, _ = await runtime.create_run("daily_learning", "old-complete")
        paused, _ = await runtime.create_run("daily_learning", "old-paused")
        await runtime.run_store.set_status(completed.run_id, "running")
        await runtime.run_store.set_status(completed.run_id, "completed")
        await runtime.run_store.set_status(paused.run_id, "running")
        await runtime.run_store.set_status(paused.run_id, "awaiting_input")

    connection = await aiosqlite.connect(settings.checkpoint_path.as_posix())
    await connection.execute(
        "UPDATE learnloop_agent_runs SET updated_at = ?",
        ("2000-01-01T00:00:00+00:00",),
    )
    await connection.commit()
    await connection.close()

    async with open_agent_runtime(settings, dependencies, None) as runtime:
        assert await runtime.run_store.get(completed.run_id) is None
        assert await runtime.run_store.get(paused.run_id) is not None


@pytest.mark.asyncio
async def test_same_resource_supports_multiple_engine_attempts(tmp_path: Path) -> None:
    """Run identity no longer collapses pass^k or fixed/dynamic comparisons."""

    _, dependencies, session_id, _ = await _study_setup()
    settings = Settings(environment="test", data_dir=tmp_path / "data")
    async with open_agent_runtime(settings, dependencies, None) as runtime:
        first, _ = await runtime.create_run("daily_learning", session_id)
        second, _ = await runtime.create_run("daily_learning", session_id)

    assert first.run_id != second.run_id
    assert first.attempt_no == 1
    assert second.attempt_no == 2
    assert first.engine_version == second.engine_version == "fixed_v1"


def _parse_sse(body: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for block in body.strip().split("\n\n"):
        data_line = next(
            (line for line in block.splitlines() if line.startswith("data: ")),
            None,
        )
        if data_line is None:
            continue
        decoded = json.loads(data_line.removeprefix("data: "))
        if not isinstance(decoded, dict):
            raise AssertionError("SSE data must be a JSON object")
        events.append(decoded)
    return events
