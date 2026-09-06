"""Compiled bounded daily-learning StateGraph."""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.graphs.context import DailyLearningContext
from app.agent.nodes.daily_learning import (
    evaluate_answer,
    generate_exercise,
    generate_lesson,
    generate_prerequisite_remediation,
    generate_supplemental,
    load_context,
    retrieve_sources,
    route_after_answer,
    route_after_review,
    route_by_result,
    save_summary,
    schedule_review,
    select_concepts,
    update_mastery,
    wait_for_answer,
)
from app.agent.states import StudySessionState


def build_daily_learning_graph() -> CompiledStateGraph[
    StudySessionState,
    DailyLearningContext,
    StudySessionState,
    StudySessionState,
]:
    builder = StateGraph(StudySessionState, context_schema=DailyLearningContext)
    builder.add_node("load_context", load_context)
    builder.add_node("select_concepts", select_concepts)
    builder.add_node("retrieve_sources", retrieve_sources)
    builder.add_node("generate_lesson", generate_lesson)
    builder.add_node("generate_exercise", generate_exercise)
    builder.add_node("wait_for_answer", wait_for_answer)
    builder.add_node("evaluate_answer", evaluate_answer)
    builder.add_node("route_by_result", route_by_result)
    builder.add_node("update_mastery", update_mastery)
    builder.add_node("schedule_review", schedule_review)
    builder.add_node("generate_supplemental", generate_supplemental)
    builder.add_node(
        "generate_prerequisite_remediation",
        generate_prerequisite_remediation,
    )
    builder.add_node("save_summary", save_summary)

    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "select_concepts")
    builder.add_edge("select_concepts", "retrieve_sources")
    builder.add_edge("retrieve_sources", "generate_lesson")
    builder.add_edge("generate_lesson", "generate_exercise")
    builder.add_edge("generate_exercise", "wait_for_answer")
    builder.add_conditional_edges(
        "wait_for_answer",
        route_after_answer,
        {"answer": "evaluate_answer", "wait": END},
    )
    builder.add_edge("evaluate_answer", "route_by_result")
    builder.add_edge("route_by_result", "update_mastery")
    builder.add_edge("update_mastery", "schedule_review")
    builder.add_conditional_edges(
        "schedule_review",
        route_after_review,
        {
            "complete": "save_summary",
            "supplemental": "generate_supplemental",
            "prerequisite": "generate_prerequisite_remediation",
        },
    )
    builder.add_edge("generate_supplemental", "wait_for_answer")
    builder.add_edge("generate_prerequisite_remediation", "wait_for_answer")
    builder.add_edge("save_summary", END)
    return builder.compile()
