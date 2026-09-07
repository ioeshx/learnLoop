"""Atomic SQLite operations for the persistent job queue."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from app.workers.models import BackgroundJob, JobStatus, JobType


class SqliteJobStore:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    @classmethod
    async def open(cls, path: Path) -> "SqliteJobStore":
        connection = await aiosqlite.connect(path.as_posix(), isolation_level=None)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.execute("PRAGMA busy_timeout = 5000")
        return cls(connection)

    async def close(self) -> None:
        await self._connection.close()

    async def enqueue(self, job: BackgroundJob) -> BackgroundJob:
        try:
            await self._connection.execute(
                """
                INSERT INTO background_jobs (
                    id, job_type, payload_json, result_json, status, progress,
                    progress_message, idempotency_key, attempts, max_attempts,
                    available_at, lease_owner, lease_expires_at,
                    cancel_requested, last_error, created_at, started_at,
                    finished_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.job_type.value,
                    json.dumps(job.payload, ensure_ascii=False, separators=(",", ":")),
                    None,
                    job.status.value,
                    job.progress,
                    job.progress_message,
                    job.idempotency_key,
                    job.attempts,
                    job.max_attempts,
                    _iso(job.available_at),
                    None,
                    None,
                    False,
                    None,
                    _iso(job.created_at),
                    None,
                    None,
                    _iso(job.updated_at),
                ),
            )
            await self._connection.commit()
            return job
        except aiosqlite.IntegrityError as error:
            await self._connection.rollback()
            if job.idempotency_key is None:
                raise
            existing = await self.get_by_idempotency_key(
                job.job_type, job.idempotency_key
            )
            if existing is None:
                raise
            if existing.payload != job.payload:
                raise ValueError(
                    "idempotency key was already used with a different payload"
                ) from error
            return existing

    async def get(self, job_id: str) -> BackgroundJob | None:
        cursor = await self._connection.execute(
            "SELECT * FROM background_jobs WHERE id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return _job_from_row(row) if row is not None else None

    async def get_by_idempotency_key(
        self, job_type: JobType, idempotency_key: str
    ) -> BackgroundJob | None:
        cursor = await self._connection.execute(
            """
            SELECT * FROM background_jobs
            WHERE job_type = ? AND idempotency_key = ?
            """,
            (job_type.value, idempotency_key),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return _job_from_row(row) if row is not None else None

    async def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        job_type: JobType | None = None,
        limit: int = 50,
    ) -> list[BackgroundJob]:
        clauses: list[str] = []
        parameters: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status.value)
        if job_type is not None:
            clauses.append("job_type = ?")
            parameters.append(job_type.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        cursor = await self._connection.execute(
            f"""
            SELECT * FROM background_jobs {where}
            ORDER BY created_at DESC LIMIT ?
            """,
            parameters,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_job_from_row(row) for row in rows]

    async def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: float,
    ) -> BackgroundJob | None:
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        try:
            await self._connection.execute("BEGIN IMMEDIATE")
            await self._recover_expired(now)
            cursor = await self._connection.execute(
                """
                SELECT id FROM background_jobs
                WHERE status = 'queued' AND cancel_requested = 0
                  AND available_at <= ?
                ORDER BY available_at, created_at
                LIMIT 1
                """,
                (_iso(now),),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                await self._connection.commit()
                return None
            job_id = str(row["id"])
            cursor = await self._connection.execute(
                """
                UPDATE background_jobs
                SET status = 'running', attempts = attempts + 1,
                    lease_owner = ?, lease_expires_at = ?,
                    started_at = COALESCE(started_at, ?), updated_at = ?,
                    progress_message = 'Worker 已领取任务'
                WHERE id = ? AND status = 'queued' AND cancel_requested = 0
                """,
                (
                    worker_id,
                    _iso(lease_expires_at),
                    _iso(now),
                    _iso(now),
                    job_id,
                ),
            )
            if cursor.rowcount != 1:
                await cursor.close()
                await self._connection.rollback()
                return None
            await cursor.close()
            await self._connection.commit()
            return await self.get(job_id)
        except BaseException:
            await self._connection.rollback()
            raise

    async def report_progress(
        self,
        job_id: str,
        *,
        worker_id: str,
        progress: int,
        message: str,
        now: datetime,
        lease_seconds: float,
    ) -> bool:
        cursor = await self._connection.execute(
            """
            UPDATE background_jobs
            SET progress = ?, progress_message = ?, lease_expires_at = ?,
                updated_at = ?
            WHERE id = ? AND status = 'running' AND lease_owner = ?
              AND cancel_requested = 0
            """,
            (
                min(max(progress, 0), 99),
                message[:500],
                _iso(now + timedelta(seconds=lease_seconds)),
                _iso(now),
                job_id,
                worker_id,
            ),
        )
        updated = cursor.rowcount == 1
        await cursor.close()
        await self._connection.commit()
        return updated

    async def renew_lease(
        self,
        job_id: str,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: float,
    ) -> bool:
        cursor = await self._connection.execute(
            """
            UPDATE background_jobs
            SET lease_expires_at = ?, updated_at = ?
            WHERE id = ? AND status = 'running' AND lease_owner = ?
              AND cancel_requested = 0
            """,
            (
                _iso(now + timedelta(seconds=lease_seconds)),
                _iso(now),
                job_id,
                worker_id,
            ),
        )
        updated = cursor.rowcount == 1
        await cursor.close()
        await self._connection.commit()
        return updated

    async def is_cancel_requested(self, job_id: str, worker_id: str) -> bool:
        cursor = await self._connection.execute(
            """
            SELECT cancel_requested FROM background_jobs
            WHERE id = ? AND status = 'running' AND lease_owner = ?
            """,
            (job_id, worker_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row is None or bool(row["cancel_requested"])

    async def complete(
        self,
        job_id: str,
        *,
        worker_id: str,
        result: dict[str, Any] | None,
        now: datetime,
    ) -> bool:
        cursor = await self._connection.execute(
            """
            UPDATE background_jobs
            SET status = CASE WHEN cancel_requested = 1
                              THEN 'cancelled' ELSE 'succeeded' END,
                result_json = CASE WHEN cancel_requested = 1
                                   THEN NULL ELSE ? END,
                last_error = CASE WHEN cancel_requested = 1
                                  THEN last_error ELSE NULL END,
                progress = CASE WHEN cancel_requested = 1 THEN progress ELSE 100 END,
                progress_message = CASE WHEN cancel_requested = 1
                                        THEN '任务已取消' ELSE '任务完成' END,
                lease_owner = NULL, lease_expires_at = NULL,
                finished_at = ?, updated_at = ?
            WHERE id = ? AND status = 'running' AND lease_owner = ?
            """,
            (
                (
                    json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                    if result is not None
                    else None
                ),
                _iso(now),
                _iso(now),
                job_id,
                worker_id,
            ),
        )
        updated = cursor.rowcount == 1
        await cursor.close()
        await self._connection.commit()
        return updated

    async def fail_or_retry(
        self,
        job_id: str,
        *,
        worker_id: str,
        error: str,
        retryable: bool,
        now: datetime,
        retry_base_seconds: float,
        retry_max_seconds: float,
    ) -> BackgroundJob | None:
        try:
            await self._connection.execute("BEGIN IMMEDIATE")
            job = await self.get(job_id)
            if (
                job is None
                or job.status != JobStatus.RUNNING
                or job.lease_owner != worker_id
            ):
                await self._connection.rollback()
                return None
            if job.cancel_requested:
                next_status = JobStatus.CANCELLED
                available_at = job.available_at
                message = "任务已取消"
            elif retryable and job.attempts < job.max_attempts:
                delay = min(
                    retry_max_seconds,
                    retry_base_seconds * (2 ** max(job.attempts - 1, 0)),
                )
                next_status = JobStatus.QUEUED
                available_at = now + timedelta(seconds=delay)
                message = f"将在 {delay:g} 秒后重试"
            else:
                next_status = JobStatus.FAILED
                available_at = job.available_at
                message = "任务失败"
            finished_at = (
                _iso(now)
                if next_status in {JobStatus.FAILED, JobStatus.CANCELLED}
                else None
            )
            await self._connection.execute(
                """
                UPDATE background_jobs
                SET status = ?, available_at = ?, lease_owner = NULL,
                    lease_expires_at = NULL, last_error = ?,
                    progress_message = ?, finished_at = ?, updated_at = ?
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                """,
                (
                    next_status.value,
                    _iso(available_at),
                    error[:4000],
                    message,
                    finished_at,
                    _iso(now),
                    job_id,
                    worker_id,
                ),
            )
            await self._connection.commit()
            return await self.get(job_id)
        except BaseException:
            await self._connection.rollback()
            raise

    async def request_cancel(
        self, job_id: str, *, now: datetime
    ) -> BackgroundJob | None:
        await self._connection.execute(
            """
            UPDATE background_jobs
            SET cancel_requested = 1,
                status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE status END,
                progress_message = CASE WHEN status = 'queued'
                                        THEN '任务已取消' ELSE progress_message END,
                finished_at = CASE WHEN status = 'queued' THEN ? ELSE finished_at END,
                updated_at = ?
            WHERE id = ? AND status IN ('queued', 'running')
            """,
            (_iso(now), _iso(now), job_id),
        )
        await self._connection.commit()
        return await self.get(job_id)

    async def cancel_resource_jobs(
        self, resource_id: str, *, now: datetime
    ) -> None:
        await self._connection.execute(
            """
            UPDATE background_jobs
            SET cancel_requested = 1,
                status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE status END,
                progress_message = CASE
                    WHEN status = 'queued' THEN '资料已删除，任务取消'
                    ELSE progress_message
                END,
                finished_at = CASE WHEN status = 'queued' THEN ? ELSE finished_at END,
                updated_at = ?
            WHERE job_type = ? AND json_extract(payload_json, '$.resource_id') = ?
              AND status IN ('queued', 'running')
            """,
            (_iso(now), _iso(now), JobType.RESOURCE_PROCESS.value, resource_id),
        )
        await self._connection.commit()

    async def retry(self, job_id: str, *, now: datetime) -> BackgroundJob | None:
        await self._connection.execute(
            """
            UPDATE background_jobs
            SET status = 'queued', progress = 0,
                progress_message = '等待 Worker 重新领取', attempts = 0,
                available_at = ?, lease_owner = NULL, lease_expires_at = NULL,
                cancel_requested = 0, last_error = NULL, result_json = NULL,
                started_at = NULL, finished_at = NULL, updated_at = ?
            WHERE id = ? AND status IN ('failed', 'cancelled')
            """,
            (_iso(now), _iso(now), job_id),
        )
        await self._connection.commit()
        return await self.get(job_id)

    async def _recover_expired(self, now: datetime) -> None:
        now_value = _iso(now)
        await self._connection.execute(
            """
            UPDATE background_jobs
            SET status = 'cancelled', lease_owner = NULL, lease_expires_at = NULL,
                progress_message = '任务已取消', finished_at = ?, updated_at = ?
            WHERE status = 'running' AND lease_expires_at <= ?
              AND cancel_requested = 1
            """,
            (now_value, now_value, now_value),
        )
        await self._connection.execute(
            """
            UPDATE background_jobs
            SET status = 'failed', lease_owner = NULL, lease_expires_at = NULL,
                last_error = 'Worker lease expired after final attempt',
                progress_message = 'Worker 异常退出且已耗尽重试次数',
                finished_at = ?, updated_at = ?
            WHERE status = 'running' AND lease_expires_at <= ?
              AND cancel_requested = 0 AND attempts >= max_attempts
            """,
            (now_value, now_value, now_value),
        )
        await self._connection.execute(
            """
            UPDATE background_jobs
            SET status = 'queued', available_at = ?, lease_owner = NULL,
                lease_expires_at = NULL,
                last_error = 'Worker lease expired before completion',
                progress_message = '租约过期，等待重新领取', updated_at = ?
            WHERE status = 'running' AND lease_expires_at <= ?
              AND cancel_requested = 0 AND attempts < max_attempts
            """,
            (now_value, now_value, now_value),
        )


def _job_from_row(row: aiosqlite.Row) -> BackgroundJob:
    result_value = row["result_json"]
    return BackgroundJob(
        id=str(row["id"]),
        job_type=JobType(str(row["job_type"])),
        payload=_json_object(str(row["payload_json"])),
        result=_json_object(str(result_value)) if result_value is not None else None,
        status=JobStatus(str(row["status"])),
        progress=int(row["progress"]),
        progress_message=(
            str(row["progress_message"])
            if row["progress_message"] is not None
            else None
        ),
        idempotency_key=(
            str(row["idempotency_key"])
            if row["idempotency_key"] is not None
            else None
        ),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        available_at=datetime.fromisoformat(str(row["available_at"])),
        lease_owner=(
            str(row["lease_owner"]) if row["lease_owner"] is not None else None
        ),
        lease_expires_at=(
            datetime.fromisoformat(str(row["lease_expires_at"]))
            if row["lease_expires_at"] is not None
            else None
        ),
        cancel_requested=bool(row["cancel_requested"]),
        last_error=(
            str(row["last_error"]) if row["last_error"] is not None else None
        ),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        started_at=(
            datetime.fromisoformat(str(row["started_at"]))
            if row["started_at"] is not None
            else None
        ),
        finished_at=(
            datetime.fromisoformat(str(row["finished_at"]))
            if row["finished_at"] is not None
            else None
        ),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("background job JSON must be an object")
    return parsed


def _iso(value: datetime) -> str:
    return value.isoformat()
