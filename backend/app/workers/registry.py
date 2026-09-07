"""Typed background-job handler registry."""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.workers.models import BackgroundJob, JobType


class JobContext(Protocol):
    async def report_progress(self, progress: int, message: str) -> None: ...

    async def raise_if_cancelled(self) -> None: ...


class JobHandler(Protocol):
    async def __call__(
        self, job: BackgroundJob, context: JobContext
    ) -> dict[str, Any] | None: ...


class JobHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[JobType, JobHandler] = {}

    def register(self, job_type: JobType, handler: JobHandler) -> None:
        if job_type in self._handlers:
            raise ValueError(f"handler already registered for {job_type.value}")
        self._handlers[job_type] = handler

    def handler_for(self, job_type: JobType) -> JobHandler:
        try:
            return self._handlers[job_type]
        except KeyError as error:
            raise PermanentJobError(
                f"no handler registered for {job_type.value}"
            ) from error


class PermanentJobError(Exception):
    """A malformed or unsupported job that must not be retried."""


class JobCancelledError(Exception):
    """Raised cooperatively when cancellation or lease loss is observed."""


ProgressReporter = Callable[[int, str], Awaitable[None]]

