"""End-to-end tests for the bounded LangGraph daily-learning workflow."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime
from langgraph.types import Command

from app.agent.graphs import DailyLearningContext, build_daily_learning_graph
from app.agent.nodes.daily_learning import evaluate_short_answer
from app.agent.states import StudySessionState
from app.agent.tools import LearningTools
from app.application import (
    ApplicationDependencies,
    CreateGoalCommand,
    CreateLearningGoal,
    CreateStudyPlan,
    StartSessionCommand,
    StartStudySession,
)
from app.domain.resources import ResourceCitation
from app.domain.sessions import StudySessionStatus
from app.infrastructure.llm import FakeModelProvider, StructuredModel
from app.infrastructure.review import FsrsReviewScheduler
from tests.fakes import FakeUnitOfWorkFactory

NOW = datetime(2026, 3, 1, 9, tzinfo=UTC)


class StaticResourceSearch:
    async def search_for_knowledge_node(
        self, knowledge_node_id: str, *, limit: int = 5
    ) -> list[ResourceCitation]:
        del knowledge_node_id, limit
        return [
            ResourceCitation(
                resource_id="resource-1",
                chunk_id="chunk-1",
                title="图算法手册",
                excerpt="BFS 使用先进先出的队列。",
                score=0.03,
                page_number=7,
                section="广度优先搜索",
                source_uri=None,
            )
        ]


async def _learning_run() -> tuple[
    FakeUnitOfWorkFactory,
    DailyLearningContext,
    str,
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
            title="Learn graph traversal",
            desired_outcome="Implement BFS independently",
            weekly_minutes=180,
            idempotency_key="daily-graph-goal",
        )
    )
    plan = await CreateStudyPlan(dependencies).execute(goal.id)
    session = await StartStudySession(dependencies).execute(
        StartSessionCommand(
            goal_id=goal.id,
            plan_item_id=plan.plan.items[0].id,
            idempotency_key="daily-graph-session",
        )
    )
    return (
        factory,
        DailyLearningContext(tools=LearningTools(dependencies)),
        session.session.id,
        session.exercise.answer_key[0],
        next(
            option
            for option in session.exercise.options
            if option not in session.exercise.answer_key
        ),
    )


@pytest.mark.asyncio
async def test_daily_graph_waits_then_completes_correct_answer() -> None:
    factory, context, session_id, correct_answer, _ = await _learning_run()
    graph = build_daily_learning_graph()

    awaiting = cast(
        StudySessionState,
        await graph.ainvoke(
            {"run_id": "daily-run-correct", "session_id": session_id},
            context=context,
        ),
    )

    assert awaiting["status"] == "awaiting_answer"
    assert awaiting["events"] == [
        "load_context",
        "select_concepts",
            "retrieve_sources",
            "generate_lesson",
            "generate_exercise",
        ]
    assert factory.state.attempts == {}

    completed = cast(
        StudySessionState,
        await graph.ainvoke(
            {**awaiting, "selected_options": [correct_answer]},
            context=context,
        ),
    )

    assert completed["status"] == "completed"
    assert completed["learning_outcome"] == "mastered"
    assert completed["mastery_score"] == pytest.approx(0.15)
    assert completed["attempt_number"] == 1
    assert completed["review_due_at"]
    assert len(factory.state.attempts) == 1
    assert (
        factory.state.sessions[session_id].status
        == StudySessionStatus.COMPLETED
    )


@pytest.mark.asyncio
async def test_daily_graph_adds_only_retrieved_source_citations() -> None:
    _, base_context, session_id, _, _ = await _learning_run()
    dependencies = replace(
        base_context.tools.dependencies,
        resource_search=StaticResourceSearch(),
    )
    context = DailyLearningContext(tools=LearningTools(dependencies))

    result = cast(
        StudySessionState,
        await build_daily_learning_graph().ainvoke(
            {"run_id": "rag-citation-run", "session_id": session_id},
            context=context,
        ),
    )

    assert result["source_refs"][0]["chunk_id"] == "chunk-1"
    assert "[1] 图算法手册 · 第 7 页" in result["lesson_content"]
    assert "[1] 图算法手册 · 第 7 页" in result["exercise_prompt"]


@pytest.mark.asyncio
async def test_daily_graph_bounds_remediation_to_two_retries() -> None:
    factory, context, session_id, _, wrong_answer = await _learning_run()
    graph = build_daily_learning_graph()
    state: StudySessionState = {
        "run_id": "daily-run-wrong",
        "session_id": session_id,
        "selected_options": [wrong_answer],
    }

    first = cast(
        StudySessionState, await graph.ainvoke(state, context=context)
    )
    assert first["status"] == "awaiting_answer"
    assert first["learning_outcome"] == "partially_mastered"
    assert first["remediation_count"] == 1
    assert first["selected_options"] == []
    assert "generate_supplemental" in first["events"]

    second = cast(
        StudySessionState,
        await graph.ainvoke(
            {**first, "selected_options": [wrong_answer]}, context=context
        ),
    )
    assert second["status"] == "awaiting_answer"
    assert second["learning_outcome"] == "not_mastered"
    assert second["remediation_count"] == 2
    assert "generate_prerequisite_remediation" in second["events"]

    completed = cast(
        StudySessionState,
        await graph.ainvoke(
            {**second, "selected_options": [wrong_answer]}, context=context
        ),
    )
    assert completed["status"] == "completed"
    assert completed["learning_outcome"] == "remediation_exhausted"
    assert completed["remediation_count"] == 2
    assert completed["attempt_number"] == 3
    assert len(factory.state.attempts) == 3


@pytest.mark.asyncio
async def test_short_answer_evaluation_node_uses_structured_model() -> None:
    _, base_context, _, _, _ = await _learning_run()
    provider = FakeModelProvider(
        {
            "answer_evaluation": [
                {
                    "score_ratio": 0.8,
                    "is_correct": True,
                    "feedback": "说明了先进先出与逐层展开的联系。",
                    "strengths": ["抓住了队列顺序"],
                    "improvements": ["补充访问标记的作用"],
                }
            ]
        }
    )
    context = DailyLearningContext(
        tools=base_context.tools,
        model=StructuredModel(provider),
    )

    result = await evaluate_short_answer(
        {
            "exercise_prompt": "BFS 为什么使用队列？",
            "evaluation_rubric": "说明先进先出与逐层遍历的关系",
            "reference_answer": "队列让先发现的节点先展开。",
            "learner_answer": "因为队列先进先出，所以可以逐层展开。",
        },
        Runtime(context=context),
    )

    assert result["evaluation"]["score_ratio"] == pytest.approx(0.8)
    assert result["events"] == ["evaluate_short_answer"]
    assert provider.requests[0].prompt_name == "answer_evaluation"


@pytest.mark.asyncio
async def test_wrong_answer_runs_model_diagnosis_and_remediation() -> None:
    _, base_context, session_id, _, wrong_answer = await _learning_run()
    lesson = {
        "title": "图遍历基础",
        "explanation": "先明确图遍历中的节点与边。",
        "examples": ["从起点访问相邻节点。"],
        "checkpoints": ["起点的作用是什么？"],
        "summary": "遍历从起点逐步访问节点。",
    }
    provider = FakeModelProvider(
        {
            "lesson": [lesson, {**lesson, "title": "补充讲解"}],
            "misconception_diagnosis": [
                {
                    "misconception": "混淆了概念边界与细节记忆。",
                    "evidence": ["选择了错误选项"],
                    "remediation_steps": ["重新比较三个选项"],
                    "prerequisite_node_keys": [],
                }
            ],
        }
    )
    context = DailyLearningContext(
        tools=base_context.tools,
        model=StructuredModel(provider),
    )

    result = cast(
        StudySessionState,
        await build_daily_learning_graph().ainvoke(
            {
                "run_id": "model-remediation-run",
                "session_id": session_id,
                "selected_options": [wrong_answer],
            },
            context=context,
        ),
    )

    assert result["status"] == "awaiting_answer"
    assert result["diagnosis"]["misconception"] == (
        "混淆了概念边界与细节记忆。"
    )
    assert [request.prompt_name for request in provider.requests] == [
        "lesson",
        "misconception_diagnosis",
        "lesson",
    ]


@pytest.mark.asyncio
async def test_learner_can_revise_answer_at_grade_interrupt() -> None:
    factory, base_context, session_id, correct_answer, wrong_answer = (
        await _learning_run()
    )
    context = DailyLearningContext(
        tools=base_context.tools,
        review_grades=True,
    )
    graph = build_daily_learning_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "grade-review-thread"}}

    interrupted = await graph.ainvoke(
        {
            "run_id": "grade-review-run",
            "session_id": session_id,
            "selected_options": [wrong_answer],
        },
        config=config,
        context=context,
    )
    assert interrupted["status"] == "awaiting_grade_review"
    assert interrupted["evaluation"]["is_correct"] is False

    completed = await graph.ainvoke(
        Command(
            resume={
                "action": "revise_answer",
                "selected_options": [correct_answer],
            }
        ),
        config=config,
        context=context,
    )

    assert completed["status"] == "completed"
    assert completed["learning_outcome"] == "mastered"
    attempt = next(iter(factory.state.attempts.values()))
    assert attempt.answer == (correct_answer,)
