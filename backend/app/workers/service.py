"""Application-facing operations for enqueueing and controlling jobs."""

from collections.abc import Callable
from datetime import datetime

from app.application.errors import NotFoundError
from app.application.services import DEFAULT_USER_ID
from app.workers.models import BackgroundJob, JobStatus, JobType
from app.workers.store import SqliteJobStore


class JobService:
    """面向 API 和业务流程的任务入队、查询、取消与人工重试门面。"""

    def __init__(
        self,
        *,
        store: SqliteJobStore,
        clock: Callable[[], datetime],
        max_attempts: int,
    ) -> None:
        """注入持久化队列、时钟和统一的最大尝试次数。"""

        self._store = store
        self._clock = clock
        self._max_attempts = max_attempts

    async def enqueue_resource(self, resource_id: str) -> BackgroundJob:
        """为资料解析与索引入队，并以资源 ID 生成天然幂等键。"""

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
        """创建指定目标和时间窗口的周报聚合任务。"""

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
        """创建到期复习快照任务，未指定截止时间时使用当前时钟。"""

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
        """构造领域任务并交给 Store 持久化，由唯一索引落实幂等语义。"""

        job = BackgroundJob.create(
            job_type=job_type,
            payload=payload,
            idempotency_key=idempotency_key,
            max_attempts=self._max_attempts,
            now=self._clock(),
        )
        return await self._store.enqueue(job)

    async def get(self, job_id: str) -> BackgroundJob:
        """读取任务；不存在时转换成应用层统一的 NotFoundError。"""

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
        """按可选状态和类型筛选任务，并限制返回数量。"""

        return await self._store.list_jobs(
            status=status, job_type=job_type, limit=limit
        )

    async def cancel(self, job_id: str) -> BackgroundJob:
        """请求取消任务；排队任务立即结束，运行任务由 Handler 协作式停止。"""

        if await self._store.get(job_id) is None:
            raise NotFoundError("background job", job_id)
        job = await self._store.request_cancel(job_id, now=self._clock())
        if job is None:
            raise NotFoundError("background job", job_id)
        return job

    async def cancel_resource_jobs(self, resource_id: str) -> None:
        """资料删除时取消与该资源关联的所有未完成处理任务。"""

        await self._store.cancel_resource_jobs(resource_id, now=self._clock())

    async def retry(self, job_id: str) -> BackgroundJob:
        """将失败或已取消任务重置为排队状态，拒绝重试其他状态。"""

        existing = await self.get(job_id)
        if existing.status not in {JobStatus.FAILED, JobStatus.CANCELLED}:
            raise ValueError("only failed or cancelled jobs can be retried")
        job = await self._store.retry(job_id, now=self._clock())
        if job is None:
            raise NotFoundError("background job", job_id)
        return job
