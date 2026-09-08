"""Durable Trace and structured model telemetry tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.agent.execution import SqliteAgentRunStore
from app.agent.prompts import EXERCISE_PROMPT
from app.agent.schemas import ExerciseProposal
from app.infrastructure.llm import (
    FakeModelProvider,
    ModelCallObservation,
    StructuredModel,
    StructuredOutputError,
    TokenUsage,
)

VALID_EXERCISE = {
    "exercise_type": "multiple_choice",
    "prompt": "BFS 使用什么数据结构？",
    "options": ["队列", "栈"],
    "correct_options": ["队列"],
    "explanation": "队列按发现顺序展开节点。",
    "difficulty": 2,
}


@pytest.mark.asyncio
async def test_store_persists_tool_model_and_prompt_trace(tmp_path: Path) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "trace.db")
    try:
        run, _ = await store.create_or_get("daily_learning", "session-1")
        started_at = datetime(2026, 1, 1, tzinfo=UTC)
        await store.start_tool_call(
            call_id="tool-1",
            run_id=run.run_id,
            tool_name="get_study_session",
            arguments={"session_id": "session-1"},
            started_at=started_at,
        )
        await store.finish_tool_call(
            call_id="tool-1",
            result_summary={"type": "object", "keys": ["session_id"]},
            status="succeeded",
            duration_ms=12.5,
            error=None,
            completed_at=started_at,
        )
        await store.record_model_call(
            ModelCallObservation(
                run_id=run.run_id,
                prompt_name="exercise",
                prompt_version="1.0.0",
                model="fake-model",
                usage=TokenUsage(20, 10, 30),
                duration_ms=25.0,
                attempts=1,
                repaired=False,
            )
        )

        tools = await store.list_tool_calls(run.run_id)
        models = await store.list_model_calls(run.run_id)
        prompts = await store.list_prompt_versions()
        events = await store.list_events(run.run_id)
    finally:
        await store.close()

    assert tools[0].arguments == {"session_id": "session-1"}
    assert tools[0].duration_ms == 12.5
    assert models[0].total_tokens == 30
    assert prompts[0]["prompt_version"] == "1.0.0"
    assert events[-1].event == "model_completed"


@pytest.mark.asyncio
async def test_structured_model_emits_duration_and_prompt_observation() -> None:
    observations: list[ModelCallObservation] = []

    async def observe(observation: ModelCallObservation) -> None:
        observations.append(observation)

    provider = FakeModelProvider({"exercise": [VALID_EXERCISE]})
    model = StructuredModel(provider, observer=observe)
    await model.generate(
        EXERCISE_PROMPT,
        {
            "goal": "掌握图遍历",
            "lesson_title": "BFS",
            "lesson_summary": "BFS 按层遍历。",
            "difficulty": 2,
        },
        ExerciseProposal,
    )

    assert len(observations) == 1
    assert observations[0].prompt_name == "exercise"
    assert observations[0].usage.total_tokens == 15
    assert observations[0].duration_ms >= 0


@pytest.mark.asyncio
async def test_structured_model_observes_failed_repair_attempts() -> None:
    observations: list[ModelCallObservation] = []

    async def observe(observation: ModelCallObservation) -> None:
        observations.append(observation)

    model = StructuredModel(
        FakeModelProvider({"exercise": ["not-json", "still-not-json"]}),
        observer=observe,
    )
    with pytest.raises(StructuredOutputError):
        await model.generate(
            EXERCISE_PROMPT,
            {
                "goal": "掌握图遍历",
                "lesson_title": "BFS",
                "lesson_summary": "BFS 按层遍历。",
                "difficulty": 2,
            },
            ExerciseProposal,
        )

    assert len(observations) == 1
    assert observations[0].attempts == 2
    assert observations[0].error is not None
