"""Persistent background-job domain values."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class JobType(StrEnum):
    """后台任务类型枚举，用稳定字符串把持久化记录映射到具体 Handler。"""

    RESOURCE_PROCESS = "resource.process"
    WEEKLY_REPORT = "report.weekly"
    DUE_REVIEWS = "reviews.generate_due"


class JobStatus(StrEnum):
    """后台任务生命周期状态，区分等待、执行和三种终态。"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_JOB_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
}


@dataclass(frozen=True, slots=True)
class BackgroundJob:
    """后台任务的不可变领域快照。

    该数据类同时承载任务输入、进度、重试计数、租约所有权和执行结果，是 API、
    Worker 与 SQLite Store 之间传递任务状态的统一结构；状态变化通过 Store 原子更新后
    重新构造快照，避免多个组件在内存中直接修改同一个任务对象。
    """

    id: str
    job_type: JobType
    payload: dict[str, Any]
    result: dict[str, Any] | None
    status: JobStatus
    progress: int
    progress_message: str | None
    idempotency_key: str | None
    attempts: int
    max_attempts: int
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    cancel_requested: bool
    last_error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        job_type: JobType,
        payload: dict[str, Any],
        now: datetime,
        max_attempts: int,
        idempotency_key: str | None = None,
    ) -> "BackgroundJob":
        """创建处于排队状态的任务，并规范化幂等键和初始化执行元数据。"""

        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        normalized_key = idempotency_key.strip() if idempotency_key else None
        if normalized_key == "":
            normalized_key = None
        return cls(
            id=str(uuid4()),
            job_type=job_type,
            payload=payload,
            result=None,
            status=JobStatus.QUEUED,
            progress=0,
            progress_message="等待 Worker 领取",
            idempotency_key=normalized_key,
            attempts=0,
            max_attempts=max_attempts,
            available_at=now,
            lease_owner=None,
            lease_expires_at=None,
            cancel_requested=False,
            last_error=None,
            created_at=now,
            started_at=None,
            finished_at=None,
            updated_at=now,
        )
