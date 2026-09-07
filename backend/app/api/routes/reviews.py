"""Review queue endpoints."""

from typing import Annotated

from fastapi import APIRouter, Header, Query, status
from pydantic import AwareDatetime

from app.api.dependencies import ApplicationDependenciesDep
from app.api.schemas import (
    AdaptiveRecommendationResponse,
    DeferReviewRequest,
    DueReviewResponse,
    ReviewScheduleResponse,
    SessionResponse,
    StartReviewSessionRequest,
)
from app.application import (
    DeferReview,
    GetAdaptiveRecommendation,
    GetDueReviews,
    StartReviewSession,
    StartReviewSessionCommand,
)

router = APIRouter(prefix="/reviews")


@router.get("/due", response_model=list[DueReviewResponse])
async def get_due_reviews(
    dependencies: ApplicationDependenciesDep,
    due_before: Annotated[AwareDatetime | None, Query()] = None,
) -> list[DueReviewResponse]:
    reviews = await GetDueReviews(dependencies).execute(due_before=due_before)
    return [DueReviewResponse.from_domain(review) for review in reviews]


@router.get(
    "/adaptive/{knowledge_node_id}", response_model=AdaptiveRecommendationResponse
)
async def get_adaptive_recommendation(
    knowledge_node_id: str, dependencies: ApplicationDependenciesDep
) -> AdaptiveRecommendationResponse:
    recommendation = await GetAdaptiveRecommendation(dependencies).execute(
        knowledge_node_id
    )
    return AdaptiveRecommendationResponse.from_domain(recommendation)


@router.post(
    "/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED
)
async def start_review_session(
    payload: StartReviewSessionRequest,
    dependencies: ApplicationDependenciesDep,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=200)
    ],
) -> SessionResponse:
    details = await StartReviewSession(dependencies).execute(
        StartReviewSessionCommand(
            knowledge_node_id=payload.knowledge_node_id,
            idempotency_key=idempotency_key,
        )
    )
    return SessionResponse.from_details(details)


@router.post("/{knowledge_node_id}/defer", response_model=ReviewScheduleResponse)
async def defer_review(
    knowledge_node_id: str,
    payload: DeferReviewRequest,
    dependencies: ApplicationDependenciesDep,
) -> ReviewScheduleResponse:
    schedule = await DeferReview(dependencies).execute(
        knowledge_node_id, days=payload.days
    )
    return ReviewScheduleResponse.from_domain(schedule)
