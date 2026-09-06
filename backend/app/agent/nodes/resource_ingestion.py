"""Human confirmation seam for the future resource-ingestion graph."""

from langgraph.types import interrupt

from app.agent.states import ResourceIngestionState


async def confirm_resource_ambiguities(
    state: ResourceIngestionState,
) -> ResourceIngestionState:
    ambiguities = state.get("ambiguities", [])
    if not ambiguities:
        return {"events": ["confirm_resource_ambiguities"]}
    response = interrupt(
        {
            "type": "resource_ambiguity_confirmation",
            "document_id": state.get("document_id"),
            "ambiguities": ambiguities,
        }
    )
    if not isinstance(response, dict):
        raise ValueError("resource ambiguity response must be an object")
    resolutions = response.get("resolutions")
    if not isinstance(resolutions, list) or not all(
        isinstance(item, dict) for item in resolutions
    ):
        raise ValueError("resource ambiguity response requires resolutions")
    return {
        "ambiguity_resolutions": resolutions,
        "status": "processing",
        "events": ["confirm_resource_ambiguities"],
    }
