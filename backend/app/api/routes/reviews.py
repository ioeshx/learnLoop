"""Review queue endpoints."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import AwareDatetime

from app.api.dependencies import ApplicationDependenciesDep
from app.api.schemas import DueReviewResponse
from app.application import GetDueReviews

router = APIRouter(prefix="/reviews")


@router.get("/due", response_model=list[DueReviewResponse])
async def get_due_reviews(
    dependencies: ApplicationDependenciesDep,
    due_before: Annotated[AwareDatetime | None, Query()] = None,
) -> list[DueReviewResponse]:
    reviews = await GetDueReviews(dependencies).execute(due_before=due_before)
    return [DueReviewResponse.from_domain(review) for review in reviews]
