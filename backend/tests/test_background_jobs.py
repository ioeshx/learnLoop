"""Persistent job queue, lease, retry, cancellation, and API tests."""

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.workers import (
    BackgroundJob,
    BackgroundWorker,
    JobHandlerRegistry,
    JobStatus,
    JobType,
    SqliteJobStore,
)
from app.workers.bootstrap import open_background_worker
from app.workers.registry import JobContext
from app.workers.service import JobService

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class FlakyHandler:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(
        self, job: BackgroundJob, context: JobContext
    ) -> dict[str, Any]:
        self.calls += 1
        await context.report_progress(50, "half way")
        if self.calls == 1:
            raise RuntimeError("temporary failure")
        return {"job_id": job.id, "ok": True}


@pytest.mark.asyncio
async def test_job_idempotency_lease_recovery_retry_and_progress(
    tmp_path: Path,
) -> None:
    settings = Settings(environment="test", data_dir=tmp_path / "data")
    _migrate(settings)
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=UTC))
    store = await SqliteJobStore.open(settings.database_path)
    service = JobService(store=store, clock=clock, max_attempts=3)
    first = await service.enqueue(
        JobType.WEEKLY_REPORT,
        {"days": 7},
        idempotency_key="weekly-1",
    )
    duplicate = await service.enqueue(
        JobType.WEEKLY_REPORT,
        {"days": 7},
        idempotency_key="weekly-1",
    )
    assert duplicate.id == first.id
    with pytest.raises(ValueError, match="different payload"):
        await service.enqueue(
            JobType.WEEKLY_REPORT,
            {"days": 14},
            idempotency_key="weekly-1",
        )

    claimed = await store.claim_next(
        worker_id="dead-worker", now=clock(), lease_seconds=5
    )
    assert claimed is not None
    assert claimed.attempts == 1
    clock.current += timedelta(seconds=6)

    handler = FlakyHandler()
    registry = JobHandlerRegistry()
    registry.register(JobType.WEEKLY_REPORT, handler)
    worker = BackgroundWorker(
        store=store,
        registry=registry,
        clock=clock,
        poll_interval_seconds=0.01,
        lease_seconds=5,
        retry_base_seconds=0,
        retry_max_seconds=0,
        worker_id="healthy-worker",
    )
    retried = await worker.run_once()
    assert retried is not None
    assert retried.status == JobStatus.QUEUED
    assert retried.attempts == 2
    completed = await worker.run_once()
    assert completed is not None
    assert completed.status == JobStatus.SUCCEEDED
    assert completed.progress == 100
    assert completed.attempts == 3
    assert completed.result == {"job_id": first.id, "ok": True}
    await store.close()


@pytest.mark.asyncio
async def test_queued_job_can_be_cancelled_and_manually_retried(tmp_path: Path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path / "data")
    _migrate(settings)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = await SqliteJobStore.open(settings.database_path)
    service = JobService(store=store, clock=lambda: now, max_attempts=2)
    job = await service.enqueue(
        JobType.DUE_REVIEWS,
        {"due_before": now.isoformat()},
        idempotency_key="due-1",
    )
    cancelled = await service.cancel(job.id)
    assert cancelled.status == JobStatus.CANCELLED
    assert cancelled.cancel_requested is True
    queued = await service.retry(job.id)
    assert queued.status == JobStatus.QUEUED
    assert queued.cancel_requested is False
    assert queued.attempts == 0
    await store.close()


@pytest.mark.asyncio
async def test_job_api_enqueues_idempotent_maintenance_tasks(tmp_path: Path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path / "data")
    _migrate(settings)
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            headers = {"Idempotency-Key": "report-2026-w01"}
            first = await client.post(
                "/api/v1/jobs/weekly-reports",
                headers=headers,
                json={"days": 7},
            )
            second = await client.post(
                "/api/v1/jobs/weekly-reports",
                headers=headers,
                json={"days": 7},
            )
            assert first.status_code == 202
            assert second.json()["id"] == first.json()["id"]
            due = await client.post(
                "/api/v1/jobs/due-reviews",
                headers={"Idempotency-Key": "due-2026-w01"},
                json={},
            )
            assert due.status_code == 202
            listed = await client.get(
                "/api/v1/jobs", params={"status": "queued"}
            )
            assert {job["id"] for job in listed.json()} == {
                first.json()["id"],
                due.json()["id"],
            }

            async with open_background_worker(settings) as worker:
                await worker.run_once()
                await worker.run_once()
            report_result = (await client.get(
                f"/api/v1/jobs/{first.json()['id']}"
            )).json()
            due_result = (await client.get(
                f"/api/v1/jobs/{due.json()['id']}"
            )).json()
            assert report_result["status"] == "succeeded"
            assert report_result["result"]["sessions_started"] == 0
            assert due_result["status"] == "succeeded"
            assert due_result["result"]["tasks"] == []


def _migrate(settings: Settings) -> None:
    environment = os.environ.copy()
    environment["LEARNLOOP_ENVIRONMENT"] = settings.environment
    environment["LEARNLOOP_DATA_DIR"] = str(settings.data_dir)
    subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "migrate.py"), "upgrade"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
    )
