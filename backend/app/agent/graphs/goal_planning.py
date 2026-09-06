"""Compiled goal-planning StateGraph with explicit human approval."""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.graphs.context import GoalPlanningContext
from app.agent.nodes.goal_planning import (
    build_knowledge_graph,
    check_missing_information,
    diagnostic,
    generate_plan,
    persist_plan,
    route_after_approval,
    route_after_missing,
    understand_goal,
    validate_graph,
    wait_for_approval,
)
from app.agent.states import GoalPlanningState


def build_goal_planning_graph(
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[
    GoalPlanningState,
    GoalPlanningContext,
    GoalPlanningState,
    GoalPlanningState,
]:
    builder = StateGraph(GoalPlanningState, context_schema=GoalPlanningContext)
    builder.add_node("understand_goal", understand_goal)
    builder.add_node("check_missing_information", check_missing_information)
    builder.add_node("diagnostic", diagnostic)
    builder.add_node("build_knowledge_graph", build_knowledge_graph)
    builder.add_node("validate_graph", validate_graph)
    builder.add_node("generate_plan", generate_plan)
    builder.add_node("wait_for_approval", wait_for_approval)
    builder.add_node("persist_plan", persist_plan)

    builder.add_edge(START, "understand_goal")
    builder.add_edge("understand_goal", "check_missing_information")
    builder.add_conditional_edges(
        "check_missing_information",
        route_after_missing,
        {"continue": "diagnostic", "wait": END},
    )
    builder.add_edge("diagnostic", "build_knowledge_graph")
    builder.add_edge("build_knowledge_graph", "validate_graph")
    builder.add_edge("validate_graph", "generate_plan")
    builder.add_edge("generate_plan", "wait_for_approval")
    builder.add_conditional_edges(
        "wait_for_approval",
        route_after_approval,
        {"persist": "persist_plan", "wait": END},
    )
    builder.add_edge("persist_plan", END)
    return builder.compile(checkpointer=checkpointer)
