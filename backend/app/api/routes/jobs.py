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
    """按状态和类型查询最近任务，并转换为稳定的 HTTP 响应结构。"""

    jobs = await service.list_jobs(
        status=job_status, job_type=job_type, limit=limit
    )
    return [BackgroundJobResponse.from_domain(job) for job in jobs]


@router.get("/{job_id}", response_model=BackgroundJobResponse)
async def get_job(job_id: str, service: JobServiceDep) -> BackgroundJobResponse:
    """返回单个后台任务的状态、进度、结果和错误信息。"""

    return BackgroundJobResponse.from_domain(await service.get(job_id))


@router.post("/{job_id}/cancel", response_model=BackgroundJobResponse)
async def cancel_job(job_id: str, service: JobServiceDep) -> BackgroundJobResponse:
    """请求取消任务；具体即时或协作式取消语义由 JobService 决定。"""

    return BackgroundJobResponse.from_domain(await service.cancel(job_id))


@router.post("/{job_id}/retry", response_model=BackgroundJobResponse)
async def retry_job(job_id: str, service: JobServiceDep) -> BackgroundJobResponse:
    """人工重试失败或已取消任务，并将非法状态转换为 HTTP 409。"""

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
    """接受周报参数并以调用方幂等键创建异步聚合任务。"""

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
    """接受复习截止时间并以调用方幂等键创建队列快照任务。"""

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
