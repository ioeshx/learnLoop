"""Internal policy optimization, reward, replay, and dataset endpoints."""

from fastapi import APIRouter, HTTPException, status

from app.agent.execution import AgentRuntime
from app.agent.optimization import (
    BanditDecision,
    DelayedLearningOutcome,
    ExperimentEvaluationRequest,
    ExperimentReport,
    FailureCluster,
    PolicyActivationRequest,
    PolicyOptimizationService,
    PolicyRevisionRequest,
    PolicyVersion,
    PreferencePair,
    PreferencePairRequest,
    RewardRecord,
    SFTTrajectory,
    TrajectoryReview,
    TrajectoryReviewRequest,
)
from app.api.dependencies import AgentRuntimeDep

router = APIRouter(prefix="/agent/optimization")


@router.get("/policies", response_model=list[PolicyVersion])
async def list_policy_versions(runtime: AgentRuntimeDep) -> list[PolicyVersion]:
    return await _service(runtime).list_policies()


@router.post("/policies/{policy_id}/revisions", response_model=PolicyVersion)
async def revise_policy(
    policy_id: str,
    payload: PolicyRevisionRequest,
    runtime: AgentRuntimeDep,
) -> PolicyVersion:
    service = _admin_service(runtime)
    try:
        return await service.revise_policy(policy_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/policies/{policy_id}/activate", response_model=PolicyVersion)
async def activate_policy(
    policy_id: str,
    payload: PolicyActivationRequest,
    runtime: AgentRuntimeDep,
) -> PolicyVersion:
    service = _admin_service(runtime)
    try:
        return await service.activate_policy(policy_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/rewards", response_model=list[RewardRecord])
async def list_rewards(runtime: AgentRuntimeDep) -> list[RewardRecord]:
    service = _service(runtime)
    return [
        RewardRecord.model_validate_json(item)
        for item in await service.store.list_rewards()
    ]


@router.get("/failure-clusters", response_model=list[FailureCluster])
async def list_failure_clusters(runtime: AgentRuntimeDep) -> list[FailureCluster]:
    return await _service(runtime).analyze_failures()


@router.post("/runs/{run_id}/outcome", response_model=RewardRecord)
async def submit_delayed_outcome(
    run_id: str,
    payload: DelayedLearningOutcome,
    runtime: AgentRuntimeDep,
) -> RewardRecord:
    service = _admin_service(runtime)
    try:
        reward = await service.submit_delayed_outcome(run_id, payload)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await runtime.run_store.append_event(
        run_id,
        "reward_matured",
        data={
            "reward_id": reward.id,
            "reward_version": reward.reward_version,
            "optimization_score": reward.optimization_score,
            "hard_gate_passed": reward.hard_gate_passed,
        },
    )
    return reward


@router.post("/runs/{run_id}/review", response_model=TrajectoryReview)
async def review_trajectory(
    run_id: str,
    payload: TrajectoryReviewRequest,
    runtime: AgentRuntimeDep,
) -> TrajectoryReview:
    if await runtime.run_store.get(run_id) is None:
        raise HTTPException(status_code=404, detail="agent run was not found")
    service = _admin_service(runtime)
    review = TrajectoryReview(run_id=run_id, **payload.model_dump())
    await service.save_review(review)
    return review


@router.get("/datasets/sft", response_model=list[SFTTrajectory])
async def export_sft_dataset(runtime: AgentRuntimeDep) -> list[SFTTrajectory]:
    return await _admin_service(runtime).export_sft()


@router.post("/preferences", response_model=PreferencePair)
async def create_preference_pair(
    payload: PreferencePairRequest,
    runtime: AgentRuntimeDep,
) -> PreferencePair:
    service = _admin_service(runtime)
    try:
        return await service.create_preference_pair(**payload.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/preferences", response_model=list[PreferencePair])
async def list_preference_pairs(runtime: AgentRuntimeDep) -> list[PreferencePair]:
    return await _admin_service(runtime).list_preference_pairs()


@router.get("/decisions", response_model=list[BanditDecision])
async def list_bandit_decisions(runtime: AgentRuntimeDep) -> list[BanditDecision]:
    service = _service(runtime)
    return [
        BanditDecision.model_validate_json(item)
        for item in await service.store.list_bandit_decisions()
    ]


@router.post("/experiments/evaluate", response_model=ExperimentReport)
async def evaluate_policy_experiment(
    payload: ExperimentEvaluationRequest,
    runtime: AgentRuntimeDep,
) -> ExperimentReport:
    service = _admin_service(runtime)
    try:
        return await service.evaluate_experiment(
            payload.manifest, payload.samples, split=payload.split
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/experiments", response_model=list[ExperimentReport])
async def list_policy_experiments(
    runtime: AgentRuntimeDep,
) -> list[ExperimentReport]:
    return await _service(runtime).list_experiments()


def _service(runtime: AgentRuntime) -> PolicyOptimizationService:
    if runtime.optimization is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="policy optimization service is unavailable",
        )
    return runtime.optimization


def _admin_service(runtime: AgentRuntime) -> PolicyOptimizationService:
    if not runtime.policy_admin_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="policy optimization administration is disabled",
        )
    return _service(runtime)
