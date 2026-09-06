"""Learning goal endpoints."""

from typing import Annotated

from fastapi import APIRouter, Header, status

from app.api.dependencies import ApplicationDependenciesDep
from app.api.schemas import CreateGoalRequest, GoalResponse
from app.application import CreateGoalCommand, CreateLearningGoal, GetLearningGoal

router = APIRouter(prefix="/goals")
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=200,
        pattern=r".*\S.*",
    ),
]


@router.post("", response_model=GoalResponse, status_code=status.HTTP_201_CREATED)
async def create_goal(
    payload: CreateGoalRequest,
    idempotency_key: IdempotencyKey,
    dependencies: ApplicationDependenciesDep,
) -> GoalResponse:
    goal = await CreateLearningGoal(dependencies).execute(
        CreateGoalCommand(
            title=payload.title,
            description=payload.description,
            desired_outcome=payload.desired_outcome,
            weekly_minutes=payload.weekly_minutes,
            target_date=payload.target_date,
            idempotency_key=idempotency_key,
        )
    )
    return GoalResponse.from_domain(goal)


@router.get("/{goal_id}", response_model=GoalResponse)
async def get_goal(
    goal_id: str, dependencies: ApplicationDependenciesDep
) -> GoalResponse:
    goal = await GetLearningGoal(dependencies).execute(goal_id)
    return GoalResponse.from_domain(goal)
