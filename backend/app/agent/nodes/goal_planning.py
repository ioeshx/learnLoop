"""Nodes for the model-backed, approval-gated goal-planning workflow."""

from typing import Literal, cast

from langgraph.runtime import Runtime

from app.agent.graphs.context import GoalPlanningContext
from app.agent.prompts import (
    GOAL_CLARIFICATION_PROMPT,
    KNOWLEDGE_GRAPH_PROMPT,
    STUDY_PLAN_PROMPT,
)
from app.agent.schemas import (
    GoalClarification,
    KnowledgeGraphProposal,
    StudyPlanProposal,
)
from app.agent.states import GoalPlanningState


async def understand_goal(
    state: GoalPlanningState, runtime: Runtime[GoalPlanningContext]
) -> GoalPlanningState:
    goal_id = _required_string(state, "goal_id")
    goal = await runtime.context.tools.get_learning_goal(goal_id)
    should_revisit = (
        state.get("status") == "needs_clarification"
        and bool(state.get("clarification_answers"))
    )
    if state.get("clarification") and not should_revisit:
        return {"goal": goal, "events": ["understand_goal"]}

    description = cast(str, goal["description"])
    answers = state.get("clarification_answers", {})
    if answers:
        rendered_answers = "\n".join(
            f"问题：{question}\n回答：{answer}"
            for question, answer in answers.items()
        )
        description = f"{description}\n\n补充回答：\n{rendered_answers}".strip()
    result = await runtime.context.model.generate(
        GOAL_CLARIFICATION_PROMPT,
        {
            "goal_title": cast(str, goal["title"]),
            "goal_description": description,
            "desired_outcome": cast(str, goal["desired_outcome"]),
            "weekly_minutes": cast(int, goal["weekly_minutes"]),
        },
        GoalClarification,
    )
    return {
        "run_id": state.get("run_id", goal_id),
        "goal": goal,
        "clarification": result.value.model_dump(mode="json"),
        "status": "running",
        "events": ["understand_goal"],
    }


async def check_missing_information(
    state: GoalPlanningState, runtime: Runtime[GoalPlanningContext]
) -> GoalPlanningState:
    del runtime
    clarification = _required_dict(state, "clarification")
    questions = clarification.get("clarification_questions", [])
    if not isinstance(questions, list) or not all(
        isinstance(question, str) for question in questions
    ):
        raise ValueError("clarification questions must be a list of strings")
    answers = state.get("clarification_answers", {})
    missing = [question for question in questions if not answers.get(question)]
    return {
        "missing_information": missing,
        "status": "needs_clarification" if missing else "running",
        "events": ["check_missing_information"],
    }


async def route_after_missing(
    state: GoalPlanningState,
) -> Literal["continue", "wait"]:
    return "wait" if state.get("missing_information") else "continue"


async def diagnostic(
    state: GoalPlanningState, runtime: Runtime[GoalPlanningContext]
) -> GoalPlanningState:
    del runtime
    goal = _required_dict(state, "goal")
    clarification = _required_dict(state, "clarification")
    assumptions = clarification.get("assumptions", [])
    return {
        "diagnostic": {
            "weekly_minutes": goal.get("weekly_minutes"),
            "assumptions": assumptions if isinstance(assumptions, list) else [],
            "evidence": "learner_goal",
        },
        "events": ["diagnostic"],
    }


async def build_knowledge_graph(
    state: GoalPlanningState, runtime: Runtime[GoalPlanningContext]
) -> GoalPlanningState:
    if state.get("knowledge_graph"):
        return {"events": ["build_knowledge_graph"]}
    goal = _required_dict(state, "goal")
    clarification = _required_dict(state, "clarification")
    result = await runtime.context.model.generate(
        KNOWLEDGE_GRAPH_PROMPT,
        {
            "refined_goal": _dict_string(clarification, "refined_goal"),
            "desired_outcome": _dict_string(clarification, "desired_outcome"),
            "weekly_minutes": cast(int, goal["weekly_minutes"]),
        },
        KnowledgeGraphProposal,
    )
    return {
        "knowledge_graph": result.value.model_dump(mode="json"),
        "events": ["build_knowledge_graph"],
    }


async def validate_graph(
    state: GoalPlanningState, runtime: Runtime[GoalPlanningContext]
) -> GoalPlanningState:
    runtime.context.tools.validate_knowledge_graph(
        _required_string(state, "goal_id"),
        _required_dict(state, "knowledge_graph"),
    )
    return {"events": ["validate_graph"]}


async def generate_plan(
    state: GoalPlanningState, runtime: Runtime[GoalPlanningContext]
) -> GoalPlanningState:
    if state.get("study_plan"):
        return {"events": ["generate_plan"]}
    goal = _required_dict(state, "goal")
    clarification = _required_dict(state, "clarification")
    graph = _required_dict(state, "knowledge_graph")
    nodes = graph.get("nodes")
    edges = graph.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("knowledge graph must contain node and edge lists")
    result = await runtime.context.model.generate(
        STUDY_PLAN_PROMPT,
        {
            "goal": _dict_string(clarification, "desired_outcome"),
            "weekly_minutes": cast(int, goal["weekly_minutes"]),
            "knowledge_nodes": nodes,
            "prerequisite_edges": edges,
        },
        StudyPlanProposal,
    )
    proposal_data = result.value.model_dump(mode="json")
    runtime.context.tools.validate_study_plan(proposal_data, graph)
    return {"study_plan": proposal_data, "events": ["generate_plan"]}


async def wait_for_approval(
    state: GoalPlanningState, runtime: Runtime[GoalPlanningContext]
) -> GoalPlanningState:
    del runtime
    if "approved" not in state:
        status: Literal["awaiting_approval", "rejected", "running"] = (
            "awaiting_approval"
        )
    elif state["approved"]:
        status = "running"
    else:
        status = "rejected"
    return {"status": status, "events": ["wait_for_approval"]}


async def route_after_approval(
    state: GoalPlanningState,
) -> Literal["persist", "wait"]:
    return "persist" if state.get("approved") is True else "wait"


async def persist_plan(
    state: GoalPlanningState, runtime: Runtime[GoalPlanningContext]
) -> GoalPlanningState:
    persisted = await runtime.context.tools.save_plan_proposal(
        _required_string(state, "goal_id"),
        _required_dict(state, "knowledge_graph"),
        _required_dict(state, "study_plan"),
    )
    return {
        "plan_id": cast(str, persisted["plan_id"]),
        "status": "completed",
        "events": ["persist_plan"],
    }


def _required_string(state: GoalPlanningState, key: str) -> str:
    value = state.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"goal-planning state requires '{key}'")
    return value


def _required_dict(
    state: GoalPlanningState, key: str
) -> dict[str, object]:
    value = state.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"goal-planning state requires '{key}'")
    return value


def _dict_string(values: dict[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"goal-planning artifact requires '{key}'")
    return value
