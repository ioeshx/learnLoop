"""Persistent background-job domain values."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class JobType(StrEnum):
    RESOURCE_PROCESS = "resource.process"
    WEEKLY_REPORT = "report.weekly"
    DUE_REVIEWS = "reviews.generate_due"


class JobStatus(StrEnum):
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

