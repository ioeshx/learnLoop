"""Internal Model Gateway route audit, profile and circuit health endpoints."""

from fastapi import APIRouter, HTTPException, Query, status

from app.agent.execution import AgentRuntime
from app.api.dependencies import AgentRuntimeDep
from app.infrastructure.llm.gateway import (
    ModelGatewayProvider,
    ModelProfile,
    ModelRouteRecord,
    ProviderHealth,
)

router = APIRouter(prefix="/agent/model-gateway")


@router.get("/profiles", response_model=list[ModelProfile])
async def list_model_profiles(runtime: AgentRuntimeDep) -> list[ModelProfile]:
    return _gateway(runtime).router.profiles


@router.get("/health", response_model=list[ProviderHealth])
async def list_model_health(runtime: AgentRuntimeDep) -> list[ProviderHealth]:
    return _gateway(runtime).breakers.snapshots()


@router.get("/routes", response_model=list[ModelRouteRecord])
async def list_model_routes(
    runtime: AgentRuntimeDep,
    run_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1_000),
) -> list[ModelRouteRecord]:
    return [
        ModelRouteRecord.model_validate_json(item)
        for item in await runtime.run_store.list_model_routes(
            run_id=run_id, limit=limit
        )
    ]


def _gateway(runtime: AgentRuntime) -> ModelGatewayProvider:
    if runtime.model is None or not isinstance(
        runtime.model.provider, ModelGatewayProvider
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model Gateway is unavailable",
        )
    return runtime.model.provider
