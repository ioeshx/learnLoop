"""Research Tutor API with persisted evidence and citation traces."""

from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.agent.research import ResearchResult, ResearchTrace
from app.agent.research.models import RetrievalMode
from app.api.dependencies import ResearchTutorDep
from app.application.errors import NotFoundError

router = APIRouter(prefix="/research/runs")


class StartResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=4_000)
    goal_id: str = Field(min_length=1)
    knowledge_node_id: str | None = None
    mode_override: RetrievalMode | None = None


@router.post("", response_model=ResearchResult, status_code=status.HTTP_201_CREATED)
async def start_research(
    payload: StartResearchRequest, tutor: ResearchTutorDep
) -> ResearchResult:
    return await tutor.run(
        tutor.request_for(
            payload.question,
            goal_id=payload.goal_id,
            knowledge_node_id=payload.knowledge_node_id,
            mode_override=payload.mode_override,
        )
    )


@router.get("", response_model=list[ResearchTrace])
async def list_research_runs(
    tutor: ResearchTutorDep,
    goal_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ResearchTrace]:
    return await tutor.list(goal_id=goal_id, limit=limit)


@router.get("/{trace_id}", response_model=ResearchTrace)
async def get_research_run(
    trace_id: str, tutor: ResearchTutorDep
) -> ResearchTrace:
    trace = await tutor.get(trace_id)
    if trace is None:
        raise NotFoundError("research run", trace_id)
    return trace
