"""Typed background-job handler registry."""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.workers.models import BackgroundJob, JobType


class JobContext(Protocol):
    """Handler 可使用的受限运行上下文，用于进度上报和协作式取消。"""

    async def report_progress(self, progress: int, message: str) -> None:
        """持久化进度并续租，使长任务保持可观测且不会被其他 Worker 领取。"""
        ...

    async def raise_if_cancelled(self) -> None:
        """在用户取消或租约失效时抛出取消异常，让 Handler 尽快安全退出。"""
        ...


class JobHandler(Protocol):
    """后台任务处理器协议，将一个任务转换为可持久化的结果对象。"""

    async def __call__(
        self, job: BackgroundJob, context: JobContext
    ) -> dict[str, Any] | None:
        """执行任务；实现通过上下文报告进度，并返回 JSON 对象形式的结果。"""
        ...


class JobHandlerRegistry:
    """维护任务类型到 Handler 的显式映射，供 Worker 在运行时安全分派任务。"""

    def __init__(self) -> None:
        """初始化空注册表，由 Worker 启动过程注册第一方 Handler。"""

        self._handlers: dict[JobType, JobHandler] = {}

    def register(self, job_type: JobType, handler: JobHandler) -> None:
        """注册一种任务处理器，并拒绝重复注册以避免启动顺序覆盖实现。"""

        if job_type in self._handlers:
            raise ValueError(f"handler already registered for {job_type.value}")
        self._handlers[job_type] = handler

    def handler_for(self, job_type: JobType) -> JobHandler:
        """返回指定类型的 Handler；缺失映射被视为不可重试的配置错误。"""

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
