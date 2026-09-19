"""Internal Agent Team registry, execution and provenance endpoints."""

from fastapi import APIRouter, HTTPException, Query, status

from app.agent.execution import AgentRuntime
from app.agent.team import (
    AgentCard,
    AgentTeamService,
    TeamArtifact,
    TeamRunRequest,
    TeamRunResult,
    TeamTask,
)
from app.api.dependencies import AgentRuntimeDep

router = APIRouter(prefix="/agent/team")


@router.get("/roles", response_model=list[AgentCard])
async def list_team_roles(runtime: AgentRuntimeDep) -> list[AgentCard]:
    return _service(runtime).registry.cards()


@router.get("/tasks", response_model=list[TeamTask])
async def list_team_tasks(
    runtime: AgentRuntimeDep,
    parent_run_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1_000),
) -> list[TeamTask]:
    return await _service(runtime).list_tasks(
        parent_run_id=parent_run_id, limit=limit
    )


@router.get("/artifacts", response_model=list[TeamArtifact])
async def list_team_artifacts(
    runtime: AgentRuntimeDep,
    parent_run_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1_000),
) -> list[TeamArtifact]:
    return await _service(runtime).list_artifacts(
        parent_run_id=parent_run_id, limit=limit
    )


@router.post("/execute", response_model=TeamRunResult)
async def execute_team(
    payload: TeamRunRequest,
    runtime: AgentRuntimeDep,
) -> TeamRunResult:
    if not runtime.team_admin_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent Team administration is disabled",
        )
    return await _service(runtime).execute(payload)


def _service(runtime: AgentRuntime) -> AgentTeamService:
    if runtime.team is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent Team service is unavailable",
        )
    return runtime.team
