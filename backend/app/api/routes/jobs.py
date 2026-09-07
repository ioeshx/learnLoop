"""Background-job status and control endpoints."""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status

from app.api.dependencies import JobServiceDep
from app.api.schemas import (
    BackgroundJobResponse,
    DueReviewJobRequest,
    WeeklyReportJobRequest,
)
from app.workers import JobStatus, JobType

router = APIRouter(prefix="/jobs")


@router.get("", response_model=list[BackgroundJobResponse])
async def list_jobs(
    service: JobServiceDep,
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
    job_type: JobType | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[BackgroundJobResponse]:
    jobs = await service.list_jobs(
        status=job_status, job_type=job_type, limit=limit
    )
    return [BackgroundJobResponse.from_domain(job) for job in jobs]


@router.get("/{job_id}", response_model=BackgroundJobResponse)
async def get_job(job_id: str, service: JobServiceDep) -> BackgroundJobResponse:
    return BackgroundJobResponse.from_domain(await service.get(job_id))


@router.post("/{job_id}/cancel", response_model=BackgroundJobResponse)
async def cancel_job(job_id: str, service: JobServiceDep) -> BackgroundJobResponse:
    return BackgroundJobResponse.from_domain(await service.cancel(job_id))


@router.post("/{job_id}/retry", response_model=BackgroundJobResponse)
async def retry_job(job_id: str, service: JobServiceDep) -> BackgroundJobResponse:
    try:
        job = await service.retry(job_id)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error
    return BackgroundJobResponse.from_domain(job)


@router.post(
    "/weekly-reports",
    response_model=BackgroundJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_weekly_report_job(
    payload: WeeklyReportJobRequest,
    service: JobServiceDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
) -> BackgroundJobResponse:
    try:
        job = await service.enqueue_weekly_report(
            idempotency_key=idempotency_key,
            goal_id=payload.goal_id,
            days=payload.days,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error
    return BackgroundJobResponse.from_domain(job)


@router.post(
    "/due-reviews",
    response_model=BackgroundJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_due_review_job(
    payload: DueReviewJobRequest,
    service: JobServiceDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
) -> BackgroundJobResponse:
    try:
        job = await service.enqueue_due_reviews(
            idempotency_key=idempotency_key,
            due_before=payload.due_before,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error
    return BackgroundJobResponse.from_domain(job)
