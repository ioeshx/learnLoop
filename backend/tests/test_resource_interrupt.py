"""Future resource ingestion can pause on ambiguous parsing decisions."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agent.nodes.resource_ingestion import confirm_resource_ambiguities
from app.agent.states import ResourceIngestionState


@pytest.mark.asyncio
async def test_resource_ambiguity_interrupt_resumes_with_resolution() -> None:
    builder = StateGraph(ResourceIngestionState)
    builder.add_node("confirm", confirm_resource_ambiguities)
    builder.add_edge(START, "confirm")
    builder.add_edge("confirm", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "resource-thread"}}

    interrupted = await graph.ainvoke(
        {
            "run_id": "resource-run",
            "document_id": "document-1",
            "ambiguities": [
                {"field": "heading_level", "candidates": [2, 3]}
            ],
            "status": "awaiting_confirmation",
        },
        config=config,
    )
    assert interrupted["status"] == "awaiting_confirmation"
    assert interrupted["__interrupt__"][0].value["type"] == (
        "resource_ambiguity_confirmation"
    )

    resumed = await graph.ainvoke(
        Command(
            resume={
                "resolutions": [{"field": "heading_level", "selected": 2}]
            }
        ),
        config=config,
    )
    assert resumed["status"] == "processing"
    assert resumed["ambiguity_resolutions"] == [
        {"field": "heading_level", "selected": 2}
    ]
