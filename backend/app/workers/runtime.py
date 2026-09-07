"""Single-process polling worker with leases and bounded retries."""

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from uuid import uuid4

from app.workers.models import BackgroundJob
from app.workers.registry import (
    JobCancelledError,
    JobContext,
    JobHandlerRegistry,
    PermanentJobError,
)
from app.workers.store import SqliteJobStore

logger = logging.getLogger(__name__)


class BackgroundWorker:
    def __init__(
        self,
        *,
        store: SqliteJobStore,
        registry: JobHandlerRegistry,
        clock: Callable[[], datetime],
        poll_interval_seconds: float,
        lease_seconds: float,
        retry_base_seconds: float,
        retry_max_seconds: float,
        worker_id: str | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._clock = clock
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._store_lock = asyncio.Lock()
        self.worker_id = worker_id or f"learnloop-{uuid4()}"

    async def run_once(self) -> BackgroundJob | None:
        async with self._store_lock:
            job = await self._store.claim_next(
                worker_id=self.worker_id,
                now=self._clock(),
                lease_seconds=self._lease_seconds,
            )
        if job is None:
            return None
        context = _WorkerJobContext(self, job.id)
        heartbeat = asyncio.create_task(context.keep_lease_alive())
        try:
            handler = self._registry.handler_for(job.job_type)
            await context.raise_if_cancelled()
            result = await handler(job, context)
            await context.raise_if_cancelled()
            async with self._store_lock:
                await self._store.complete(
                    job.id,
                    worker_id=self.worker_id,
                    result=result,
                    now=self._clock(),
                )
        except JobCancelledError as error:
            async with self._store_lock:
                await self._store.fail_or_retry(
                    job.id,
                    worker_id=self.worker_id,
                    error=str(error) or "job cancellation requested",
                    retryable=False,
                    now=self._clock(),
                    retry_base_seconds=self._retry_base_seconds,
                    retry_max_seconds=self._retry_max_seconds,
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            retryable = not isinstance(error, PermanentJobError)
            async with self._store_lock:
                updated = await self._store.fail_or_retry(
                    job.id,
                    worker_id=self.worker_id,
                    error=f"{type(error).__name__}: {error}",
                    retryable=retryable,
                    now=self._clock(),
                    retry_base_seconds=self._retry_base_seconds,
                    retry_max_seconds=self._retry_max_seconds,
                )
            logger.exception(
                "background job failed",
                extra={
                    "job_id": job.id,
                    "job_type": job.job_type.value,
                    "will_retry": bool(updated and updated.status.value == "queued"),
                },
            )
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        return await self._store.get(job.id)

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        stop_event = stop or asyncio.Event()
        logger.info("background worker started", extra={"worker_id": self.worker_id})
        while not stop_event.is_set():
            job = await self.run_once()
            if job is not None:
                continue
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    stop_event.wait(), timeout=self._poll_interval_seconds
                )
        logger.info("background worker stopped", extra={"worker_id": self.worker_id})


class _WorkerJobContext(JobContext):
    def __init__(self, worker: BackgroundWorker, job_id: str) -> None:
        self._worker = worker
        self._job_id = job_id
        self._lease_valid = True

    async def keep_lease_alive(self) -> None:
        interval = max(self._worker._lease_seconds / 3, 1)
        while True:
            await asyncio.sleep(interval)
            async with self._worker._store_lock:
                self._lease_valid = await self._worker._store.renew_lease(
                    self._job_id,
                    worker_id=self._worker.worker_id,
                    now=self._worker._clock(),
                    lease_seconds=self._worker._lease_seconds,
                )
            if not self._lease_valid:
                return

    async def report_progress(self, progress: int, message: str) -> None:
        async with self._worker._store_lock:
            updated = await self._worker._store.report_progress(
                self._job_id,
                worker_id=self._worker.worker_id,
                progress=progress,
                message=message,
                now=self._worker._clock(),
                lease_seconds=self._worker._lease_seconds,
            )
        if not updated:
            raise JobCancelledError("job was cancelled or its lease was lost")

    async def raise_if_cancelled(self) -> None:
        async with self._worker._store_lock:
            cancelled = await self._worker._store.is_cancel_requested(
                self._job_id, self._worker.worker_id
            )
        if not self._lease_valid or cancelled:
            raise JobCancelledError("job cancellation requested")
