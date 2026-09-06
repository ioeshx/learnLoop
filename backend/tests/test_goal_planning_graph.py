"""Integration tests for the approval-gated LangGraph planning workflow."""

from datetime import UTC, datetime
from typing import cast

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agent.graphs import GoalPlanningContext, build_goal_planning_graph
from app.agent.states import GoalPlanningState
from app.agent.tools import LearningTools
from app.application import (
    ApplicationDependencies,
    CreateGoalCommand,
    CreateLearningGoal,
    StartSessionCommand,
    StartStudySession,
)
from app.domain.exceptions import KnowledgeGraphError
from app.infrastructure.llm import FakeModelProvider, FakeOutput, StructuredModel
from app.infrastructure.review import FsrsReviewScheduler
from tests.fakes import FakeUnitOfWorkFactory

NOW = datetime(2026, 3, 2, 9, tzinfo=UTC)


def _planning_responses() -> dict[str, list[FakeOutput]]:
    return {
        "goal_clarification": [
            {
                "refined_goal": "理解图表示并独立实现 BFS",
                "desired_outcome": "使用 Python 独立实现 BFS",
                "assumptions": ["已掌握 Python 基础"],
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
                        "description": "理解队列驱动的逐层遍历。",
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
                "title": "BFS 学习计划",
                "rationale": "先掌握图表示，再实现遍历。",
                "items": [
                    {
                        "knowledge_node_key": "graph-basics",
                        "title": "掌握图表示",
                        "estimated_minutes": 25,
                        "learning_objectives": ["比较两种图表示"],
                    },
                    {
                        "knowledge_node_key": "bfs",
                        "title": "实现 BFS",
                        "estimated_minutes": 35,
                        "learning_objectives": ["使用队列完成逐层遍历"],
                    },
                ],
            }
        ],
    }


async def _context(
    responses: dict[str, list[FakeOutput]],
) -> tuple[FakeUnitOfWorkFactory, GoalPlanningContext, FakeModelProvider, str]:
    factory = FakeUnitOfWorkFactory()
    provider = FakeModelProvider(responses)
    dependencies = ApplicationDependencies(
        uow_factory=factory,
        review_scheduler=FsrsReviewScheduler(),
        clock=lambda: NOW,
    )
    goal = await CreateLearningGoal(dependencies).execute(
        CreateGoalCommand(
            title="图算法",
            desired_outcome="独立实现 BFS",
            weekly_minutes=180,
            idempotency_key="planning-graph-goal",
        )
    )
    context = GoalPlanningContext(
        tools=LearningTools(dependencies),
        model=StructuredModel(provider),
    )
    return factory, context, provider, goal.id


@pytest.mark.asyncio
async def test_goal_planning_graph_waits_for_approval_then_persists() -> None:
    factory, context, provider, goal_id = await _context(_planning_responses())
    graph = build_goal_planning_graph()

    awaiting = cast(
        GoalPlanningState,
        await graph.ainvoke(
            {"run_id": "planning-run", "goal_id": goal_id}, context=context
        ),
    )

    assert awaiting["status"] == "awaiting_approval"
    assert awaiting["missing_information"] == []
    assert awaiting["events"] == [
        "understand_goal",
        "check_missing_information",
        "diagnostic",
        "build_knowledge_graph",
        "validate_graph",
        "generate_plan",
    ]
    assert factory.state.plans == {}
    assert len(provider.requests) == 3

    completed = cast(
        GoalPlanningState,
        await graph.ainvoke({**awaiting, "approved": True}, context=context),
    )

    assert completed["status"] == "completed"
    assert completed["plan_id"] in factory.state.plans
    assert [node.title for node in factory.state.nodes.values()] == [
        "图的表示",
        "广度优先搜索",
    ]
    assert len(factory.state.exercises) == 2
    assert len(provider.requests) == 3

    plan = factory.state.plans[completed["plan_id"]]
    session = await StartStudySession(context.tools.dependencies).execute(
        StartSessionCommand(
            goal_id=goal_id,
            plan_item_id=plan.items[0].id,
            idempotency_key="planned-session",
        )
    )
    assert session.exercise.knowledge_node_id == plan.items[0].knowledge_node_id


@pytest.mark.asyncio
async def test_goal_planning_graph_stops_when_information_is_missing() -> None:
    question = "你计划使用哪种编程语言？"
    responses: dict[str, list[FakeOutput]] = {
        "goal_clarification": [
            {
                "refined_goal": "实现 BFS",
                "desired_outcome": "独立实现 BFS",
                "assumptions": [],
                "clarification_questions": [question],
            }
        ]
    }
    factory, context, provider, goal_id = await _context(responses)

    result = cast(
        GoalPlanningState,
        await build_goal_planning_graph().ainvoke(
            {"run_id": "missing-run", "goal_id": goal_id}, context=context
        ),
    )

    assert result["status"] == "needs_clarification"
    assert result["missing_information"] == [question]
    assert "knowledge_graph" not in result
    assert factory.state.plans == {}
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_goal_planning_graph_rejects_cycle_before_persistence() -> None:
    responses = _planning_responses()
    graph_response = responses["knowledge_graph"][0]
    assert isinstance(graph_response, dict)
    graph_response["edges"] = [
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
    factory, context, _, goal_id = await _context(responses)

    with pytest.raises(KnowledgeGraphError, match="cycle"):
        await build_goal_planning_graph().ainvoke(
            {"run_id": "cycle-run", "goal_id": goal_id}, context=context
        )

    assert factory.state.nodes == {}
    assert factory.state.plans == {}


@pytest.mark.asyncio
async def test_approval_interrupt_accepts_valid_plan_edit() -> None:
    factory, context, _, goal_id = await _context(_planning_responses())
    graph = build_goal_planning_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "plan-edit-thread"}}

    interrupted = await graph.ainvoke(
        {"run_id": "plan-edit-run", "goal_id": goal_id},
        config=config,
        context=context,
    )
    edited_plan = dict(interrupted["study_plan"])
    edited_items = [dict(item) for item in edited_plan["items"]]
    edited_items[0]["title"] = "先比较邻接表与邻接矩阵"
    edited_plan["items"] = edited_items

    completed = await graph.ainvoke(
        Command(resume={"action": "edit", "study_plan": edited_plan}),
        config=config,
        context=context,
    )

    assert completed["status"] == "completed"
    persisted = factory.state.plans[completed["plan_id"]]
    assert persisted.items[0].title == "先比较邻接表与邻接矩阵"
