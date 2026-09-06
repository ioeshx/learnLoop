"""Study plan endpoints."""

from fastapi import APIRouter, status

from app.api.dependencies import ApplicationDependenciesDep
from app.api.schemas import PlanResponse
from app.application import CreateStudyPlan, GetStudyPlan

router = APIRouter()


@router.post(
    "/goals/{goal_id}/plans",
    response_model=PlanResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_plan(
    goal_id: str, dependencies: ApplicationDependenciesDep
) -> PlanResponse:
    details = await CreateStudyPlan(dependencies).execute(goal_id)
    return PlanResponse.from_details(details)


@router.get("/plans/{plan_id}", response_model=PlanResponse)
async def get_plan(
    plan_id: str, dependencies: ApplicationDependenciesDep
) -> PlanResponse:
    details = await GetStudyPlan(dependencies).execute(plan_id)
    return PlanResponse.from_details(details)
