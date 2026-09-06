"""LLM curriculum integration tests using deterministic model responses."""

from datetime import UTC, datetime

import pytest

from app.application import (
    ApplicationDependencies,
    CreateGoalCommand,
    CreateLearningGoal,
    CreateStudyPlan,
    CurriculumGenerationError,
    StartSessionCommand,
    StartStudySession,
)
from app.infrastructure.llm import (
    FakeModelProvider,
    FakeOutput,
    LlmCurriculumGenerator,
    StructuredModel,
)
from app.infrastructure.review import FsrsReviewScheduler
from tests.fakes import FakeUnitOfWorkFactory

NOW = datetime(2026, 1, 1, 9, tzinfo=UTC)


def _valid_responses() -> dict[str, list[FakeOutput]]:
    return {
        "goal_clarification": [
            {
                "refined_goal": "理解图遍历并能独立实现",
                "desired_outcome": "独立实现 BFS 和 DFS",
                "assumptions": ["使用 Python"],
                "clarification_questions": [],
            }
        ],
        "knowledge_graph": [
            {
                "nodes": [
                    {
                        "key": "graph-basics",
                        "title": "图的表示",
                        "description": "理解邻接表与邻接矩阵。",
                        "difficulty": 1.5,
                    },
                    {
                        "key": "bfs",
                        "title": "广度优先搜索",
                        "description": "理解逐层遍历与队列。",
                        "difficulty": 2.5,
                    },
                ],
                "edges": [
                    {
                        "source_key": "graph-basics",
                        "target_key": "bfs",
                        "relation": "prerequisite",
                    }
                ],
            }
        ],
        "study_plan": [
            {
                "title": "图遍历学习计划",
                "rationale": "先表示图，再逐层遍历。",
                "items": [
                    {
                        "knowledge_node_key": "graph-basics",
                        "title": "建立图表示基础",
                        "estimated_minutes": 25,
                        "learning_objectives": ["区分邻接表和邻接矩阵"],
                    },
                    {
                        "knowledge_node_key": "bfs",
                        "title": "实现 BFS",
                        "estimated_minutes": 35,
                        "learning_objectives": ["使用队列实现 BFS"],
                    },
                ],
            }
        ],
        "lesson": [
            {
                "title": "图的表示",
                "explanation": "邻接表记录每个顶点的邻居。",
                "examples": ["A 的邻居是 B 和 C。"],
                "checkpoints": ["稀疏图适合哪种表示？"],
                "summary": "邻接表适合稀疏图。",
            },
            {
                "title": "广度优先搜索",
                "explanation": "BFS 使用队列逐层访问。",
                "examples": ["先访问起点，再访问一跳邻居。"],
                "checkpoints": ["队列如何保证逐层遍历？"],
                "summary": "先进先出保证逐层展开。",
            },
        ],
        "exercise": [
            {
                "exercise_type": "multiple_choice",
                "prompt": "稀疏图通常适合哪种表示？",
                "options": ["邻接表", "邻接矩阵", "二维数组总是最好"],
                "correct_options": ["邻接表"],
                "explanation": "邻接表只存实际存在的边。",
                "difficulty": 1.5,
            },
            {
                "exercise_type": "multiple_choice",
                "prompt": "BFS 的核心数据结构是什么？",
                "options": ["队列", "栈", "堆"],
                "correct_options": ["队列"],
                "explanation": "队列按发现顺序展开节点。",
                "difficulty": 2.5,
            },
        ],
    }


@pytest.mark.asyncio
async def test_llm_curriculum_runs_through_existing_application_boundary() -> None:
    provider = FakeModelProvider(_valid_responses())
    factory = FakeUnitOfWorkFactory()
    dependencies = ApplicationDependencies(
        uow_factory=factory,
        review_scheduler=FsrsReviewScheduler(),
        clock=lambda: NOW,
        curriculum_generator=LlmCurriculumGenerator(StructuredModel(provider)),
    )
    goal = await CreateLearningGoal(dependencies).execute(
        CreateGoalCommand(
            title="图算法",
            desired_outcome="独立实现 BFS 和 DFS",
            weekly_minutes=180,
            idempotency_key="llm-goal",
        )
    )

    details = await CreateStudyPlan(dependencies).execute(goal.id)
    session = await StartStudySession(dependencies).execute(
        StartSessionCommand(
            goal_id=goal.id,
            plan_item_id=details.plan.items[0].id,
            idempotency_key="llm-session",
        )
    )

    assert [item.title for item in details.plan.items] == [
        "建立图表示基础",
        "实现 BFS",
    ]
    assert "邻接表记录每个顶点的邻居" in session.knowledge_node.lesson_content
    assert session.exercise.answer_key == ("邻接表",)
    assert len(factory.state.nodes) == 2
    assert len(factory.state.exercises) == 2
    assert len(provider.requests) == 7


@pytest.mark.asyncio
async def test_llm_curriculum_rejects_cycle_before_persistence() -> None:
    responses = _valid_responses()
    graph = responses["knowledge_graph"][0]
    assert isinstance(graph, dict)
    graph["edges"] = [
        {
            "source_key": "graph-basics",
            "target_key": "bfs",
            "relation": "prerequisite",
        },
        {
            "source_key": "bfs",
            "target_key": "graph-basics",
            "relation": "prerequisite",
        },
    ]
    provider = FakeModelProvider(responses)
    factory = FakeUnitOfWorkFactory()
    dependencies = ApplicationDependencies(
        uow_factory=factory,
        review_scheduler=FsrsReviewScheduler(),
        clock=lambda: NOW,
        curriculum_generator=LlmCurriculumGenerator(StructuredModel(provider)),
    )
    goal = await CreateLearningGoal(dependencies).execute(
        CreateGoalCommand(
            title="图算法",
            desired_outcome="实现遍历",
            weekly_minutes=120,
            idempotency_key="invalid-graph-goal",
        )
    )

    with pytest.raises(CurriculumGenerationError):
        await CreateStudyPlan(dependencies).execute(goal.id)

    assert factory.state.plans == {}
    assert factory.state.nodes == {}
    assert len(provider.requests) == 2
