"""Internal v3 Agent authorization audit and simulation endpoints."""

from fastapi import APIRouter, HTTPException, Query, status

from app.agent.execution import AgentRuntime
from app.agent.policy import (
    AgentPolicyService,
    PolicyDecision,
    PolicySimulationRequest,
)
from app.api.dependencies import AgentRuntimeDep

router = APIRouter(prefix="/agent/policy")


@router.get("/decisions", response_model=list[PolicyDecision])
async def list_policy_decisions(
    runtime: AgentRuntimeDep,
    run_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1_000),
) -> list[PolicyDecision]:
    return await _service(runtime).list_decisions(run_id=run_id, limit=limit)


@router.post("/simulate", response_model=PolicyDecision)
async def simulate_policy(
    payload: PolicySimulationRequest,
    runtime: AgentRuntimeDep,
) -> PolicyDecision:
    if not runtime.agent_policy_admin_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent Policy administration is disabled",
        )
    return _service(runtime).simulate(payload.request)


def _service(runtime: AgentRuntime) -> AgentPolicyService:
    if runtime.agent_policy is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent Policy service is unavailable",
        )
    return runtime.agent_policy
