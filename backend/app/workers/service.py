"""Application-facing operations for enqueueing and controlling jobs."""

from collections.abc import Callable
from datetime import datetime

from app.application.errors import NotFoundError
from app.application.services import DEFAULT_USER_ID
from app.workers.models import BackgroundJob, JobStatus, JobType
from app.workers.store import SqliteJobStore


class JobService:
    def __init__(
        self,
        *,
        store: SqliteJobStore,
        clock: Callable[[], datetime],
        max_attempts: int,
    ) -> None:
        self._store = store
        self._clock = clock
        self._max_attempts = max_attempts

    async def enqueue_resource(self, resource_id: str) -> BackgroundJob:
        return await self.enqueue(
            JobType.RESOURCE_PROCESS,
            {"resource_id": resource_id},
            idempotency_key=f"resource-process:{resource_id}",
        )

    async def enqueue_weekly_report(
        self,
        *,
        idempotency_key: str,
        goal_id: str | None = None,
        days: int = 7,
    ) -> BackgroundJob:
        payload: dict[str, object] = {
            "user_id": DEFAULT_USER_ID,
            "days": days,
        }
        if goal_id is not None:
            payload["goal_id"] = goal_id
        return await self.enqueue(
            JobType.WEEKLY_REPORT,
            payload,
            idempotency_key=idempotency_key,
        )

    async def enqueue_due_reviews(
        self, *, idempotency_key: str, due_before: datetime | None = None
    ) -> BackgroundJob:
        cutoff = due_before or self._clock()
        return await self.enqueue(
            JobType.DUE_REVIEWS,
            {"user_id": DEFAULT_USER_ID, "due_before": cutoff.isoformat()},
            idempotency_key=idempotency_key,
        )

    async def enqueue(
        self,
        job_type: JobType,
        payload: dict[str, object],
        *,
        idempotency_key: str | None,
    ) -> BackgroundJob:
        job = BackgroundJob.create(
            job_type=job_type,
            payload=payload,
            idempotency_key=idempotency_key,
            max_attempts=self._max_attempts,
            now=self._clock(),
        )
        return await self._store.enqueue(job)

    async def get(self, job_id: str) -> BackgroundJob:
        job = await self._store.get(job_id)
        if job is None:
            raise NotFoundError("background job", job_id)
        return job

    async def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        job_type: JobType | None = None,
        limit: int = 50,
    ) -> list[BackgroundJob]:
        return await self._store.list_jobs(
            status=status, job_type=job_type, limit=limit
        )

    async def cancel(self, job_id: str) -> BackgroundJob:
        if await self._store.get(job_id) is None:
            raise NotFoundError("background job", job_id)
        job = await self._store.request_cancel(job_id, now=self._clock())
        if job is None:
            raise NotFoundError("background job", job_id)
        return job

    async def cancel_resource_jobs(self, resource_id: str) -> None:
        await self._store.cancel_resource_jobs(resource_id, now=self._clock())

    async def retry(self, job_id: str) -> BackgroundJob:
        existing = await self.get(job_id)
        if existing.status not in {JobStatus.FAILED, JobStatus.CANCELLED}:
            raise ValueError("only failed or cancelled jobs can be retried")
        job = await self._store.retry(job_id, now=self._clock())
        if job is None:
            raise NotFoundError("background job", job_id)
        return job

