"""Nodes for the model-backed, approval-gated goal-planning workflow."""

from typing import Literal, cast

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from app.agent.graphs.context import GoalPlanningContext
from app.agent.nodes.tool_events import call_sync_tool, call_tool
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
    goal = await call_tool(
        runtime,
        "get_learning_goal",
        runtime.context.tools.get_learning_goal(goal_id),
    )
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
    call_sync_tool(
        runtime,
        "validate_knowledge_graph",
        lambda: runtime.context.tools.validate_knowledge_graph(
            _required_string(state, "goal_id"),
            _required_dict(state, "knowledge_graph"),
        ),
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
    call_sync_tool(
        runtime,
        "validate_study_plan",
        lambda: runtime.context.tools.validate_study_plan(proposal_data, graph),
    )
    return {
        "study_plan": proposal_data,
        "status": "awaiting_approval",
        "events": ["generate_plan"],
    }


async def wait_for_approval(
    state: GoalPlanningState, runtime: Runtime[GoalPlanningContext]
) -> GoalPlanningState:
    if "approved" in state:
        return {
            "status": "running" if state["approved"] else "rejected",
            "events": ["wait_for_approval"],
        }
    response = interrupt(
        {
            "type": "plan_approval_required",
            "run_id": state.get("run_id"),
            "goal_id": state.get("goal_id"),
            "knowledge_graph": state.get("knowledge_graph", {}),
            "study_plan": state.get("study_plan", {}),
            "allowed_actions": ["approve", "reject", "edit"],
        }
    )
    if not isinstance(response, dict):
        raise ValueError("plan approval response must be an object")
    action = response.get("action")
    if action == "reject":
        return {
            "approved": False,
            "status": "rejected",
            "events": ["wait_for_approval"],
        }
    plan = state.get("study_plan", {})
    if action == "edit":
        edited_plan = response.get("study_plan")
        if not isinstance(edited_plan, dict):
            raise ValueError("edit action requires a study_plan object")
        plan = edited_plan
    elif action != "approve":
        raise ValueError("plan action must be 'approve', 'reject', or 'edit'")
    call_sync_tool(
        runtime,
        "validate_study_plan",
        lambda: runtime.context.tools.validate_study_plan(
            plan, _required_dict(state, "knowledge_graph")
        ),
    )
    return {
        "approved": True,
        "study_plan": plan,
        "status": "running",
        "events": ["wait_for_approval"],
    }


async def route_after_approval(
    state: GoalPlanningState,
) -> Literal["persist", "wait"]:
    return "persist" if state.get("approved") is True else "wait"


async def persist_plan(
    state: GoalPlanningState, runtime: Runtime[GoalPlanningContext]
) -> GoalPlanningState:
    persisted = await call_tool(
        runtime,
        "save_plan_proposal",
        runtime.context.tools.save_plan_proposal(
            _required_string(state, "goal_id"),
            _required_dict(state, "knowledge_graph"),
            _required_dict(state, "study_plan"),
        ),
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
