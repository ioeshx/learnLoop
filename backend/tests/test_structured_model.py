"""Structured generation and bounded repair behavior."""

import pytest

from app.agent.prompts import EXERCISE_PROMPT
from app.agent.schemas import ExerciseProposal
from app.infrastructure.llm import (
    FakeModelProvider,
    StructuredModel,
    StructuredOutputError,
    TokenUsage,
)

PROMPT_INPUT = {
    "goal": "掌握图遍历",
    "lesson_title": "BFS",
    "lesson_summary": "BFS 按层访问节点。",
    "difficulty": 2,
}
VALID_EXERCISE = {
    "exercise_type": "multiple_choice",
    "prompt": "BFS 通常使用什么数据结构？",
    "options": ["队列", "栈", "集合"],
    "correct_options": ["队列"],
    "explanation": "队列保证先发现的节点先展开。",
    "difficulty": 2,
}


@pytest.mark.asyncio
async def test_structured_model_validates_first_response_and_tracks_usage() -> None:
    provider = FakeModelProvider(
        {"exercise": [VALID_EXERCISE]},
        usage_per_response=TokenUsage(12, 8, 20),
    )

    result = await StructuredModel(provider).generate(
        EXERCISE_PROMPT, PROMPT_INPUT, ExerciseProposal
    )

    assert result.value.correct_options == ["队列"]
    assert result.usage.total_tokens == 20
    assert result.repaired is False
    assert provider.usage.request_count == 1
    assert "JSON Schema" in provider.requests[0].messages[0].content


@pytest.mark.asyncio
async def test_structured_model_repairs_invalid_output_once() -> None:
    provider = FakeModelProvider(
        {"exercise": ["not json", VALID_EXERCISE]},
        usage_per_response=TokenUsage(10, 5, 15),
    )

    result = await StructuredModel(provider).generate(
        EXERCISE_PROMPT, PROMPT_INPUT, ExerciseProposal
    )

    assert result.repaired is True
    assert result.usage.total_tokens == 30
    assert len(provider.requests) == 2
    assert provider.requests[1].is_repair is True


@pytest.mark.asyncio
async def test_structured_model_stops_after_one_failed_repair() -> None:
    provider = FakeModelProvider({"exercise": ["not json", "still not json"]})

    with pytest.raises(StructuredOutputError) as captured:
        await StructuredModel(provider).generate(
            EXERCISE_PROMPT, PROMPT_INPUT, ExerciseProposal
        )

    assert captured.value.attempts == 2
    assert len(provider.requests) == 2
