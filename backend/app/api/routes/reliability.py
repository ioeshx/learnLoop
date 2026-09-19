"""Stage 21 Agent reliability experiment and report endpoints."""

from fastapi import APIRouter, HTTPException, Query, status

from app.agent.reliability import ReliabilityReport, ReliabilityRunRequest
from app.api.dependencies import AgentRuntimeDep

router = APIRouter(prefix="/agent/reliability")


@router.get("/reports", response_model=list[ReliabilityReport])
async def list_reliability_reports(
    runtime: AgentRuntimeDep,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ReliabilityReport]:
    rows = await runtime.run_store.list_reliability_reports(limit=limit)
    return [ReliabilityReport.model_validate_json(row) for row in rows]


@router.get("/reports/{report_id}", response_model=ReliabilityReport)
async def get_reliability_report(
    report_id: str, runtime: AgentRuntimeDep
) -> ReliabilityReport:
    row = await runtime.run_store.get_reliability_report(report_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reliability Report was not found",
        )
    return ReliabilityReport.model_validate_json(row)


@router.post("/run", response_model=ReliabilityReport)
async def run_reliability_suite(
    payload: ReliabilityRunRequest,
    runtime: AgentRuntimeDep,
) -> ReliabilityReport:
    if not runtime.reliability_admin_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent Reliability administration is disabled",
        )
    if payload.trials_per_scenario > runtime.reliability_max_trials_per_scenario:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "trials_per_scenario exceeds the configured Reliability limit "
                f"({runtime.reliability_max_trials_per_scenario})"
            ),
        )
    if runtime.reliability is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent Reliability service is unavailable",
        )
    return await runtime.reliability.run(payload)
