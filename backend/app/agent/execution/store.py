"""SQLite registry for thread mappings and replayable SSE events."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite

from app.agent.execution.models import (
    AgentEvent,
    AgentRun,
    EventKind,
    GraphKind,
    ModelCallTrace,
    RunStatus,
    ToolCallTrace,
)
from app.infrastructure.llm.models import ModelCallObservation


class SqliteAgentRunStore:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection
        self._write_lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path) -> "SqliteAgentRunStore":
        connection = await aiosqlite.connect(path.as_posix())
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.execute("PRAGMA busy_timeout = 5000")
        store = cls(connection)
        await store.setup()
        return store

    async def setup(self) -> None:
        await self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS learnloop_agent_runs (
                run_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL UNIQUE,
                graph_kind TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (graph_kind, resource_id)
            );
            CREATE INDEX IF NOT EXISTS ix_learnloop_agent_runs_status_updated
                ON learnloop_agent_runs (status, updated_at);
            CREATE TABLE IF NOT EXISTS learnloop_agent_events (
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event TEXT NOT NULL,
                node TEXT,
                timestamp TEXT NOT NULL,
                data_json TEXT NOT NULL,
                PRIMARY KEY (run_id, sequence),
                FOREIGN KEY (run_id) REFERENCES learnloop_agent_runs(run_id)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS learnloop_tool_calls (
                call_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                result_summary_json TEXT,
                status TEXT NOT NULL,
                duration_ms REAL,
                error TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY (run_id) REFERENCES learnloop_agent_runs(run_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS ix_learnloop_tool_calls_run_started
                ON learnloop_tool_calls (run_id, started_at);
            CREATE TABLE IF NOT EXISTS learnloop_prompt_versions (
                prompt_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                PRIMARY KEY (prompt_name, prompt_version)
            );
            CREATE TABLE IF NOT EXISTS learnloop_model_calls (
                call_id TEXT PRIMARY KEY,
                run_id TEXT,
                prompt_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                duration_ms REAL NOT NULL,
                attempts INTEGER NOT NULL,
                repaired INTEGER NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES learnloop_agent_runs(run_id)
                    ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS ix_learnloop_model_calls_run_created
                ON learnloop_model_calls (run_id, created_at);
            """
        )
        await self._connection.commit()

    async def close(self) -> None:
        await self._connection.close()

    async def create_or_get(
        self, graph_kind: GraphKind, resource_id: str
    ) -> tuple[AgentRun, bool]:
        async with self._write_lock:
            cursor = await self._connection.execute(
                """
                SELECT * FROM learnloop_agent_runs
                WHERE graph_kind = ? AND resource_id = ?
                """,
                (graph_kind, resource_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is not None:
                return _run_from_row(row), False
            now = datetime.now(UTC)
            run = AgentRun(
                run_id=str(uuid4()),
                thread_id=str(uuid4()),
                graph_kind=graph_kind,
                resource_id=resource_id,
                status="created",
                created_at=now,
                updated_at=now,
            )
            await self._connection.execute(
                """
                INSERT INTO learnloop_agent_runs (
                    run_id, thread_id, graph_kind, resource_id, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.thread_id,
                    run.graph_kind,
                    run.resource_id,
                    run.status,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )
            await self._connection.commit()
            return run, True

    async def get(self, run_id: str) -> AgentRun | None:
        cursor = await self._connection.execute(
            "SELECT * FROM learnloop_agent_runs WHERE run_id = ?", (run_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return _run_from_row(row) if row is not None else None

    async def list_runs(self, *, limit: int = 100) -> list[AgentRun]:
        cursor = await self._connection.execute(
            "SELECT * FROM learnloop_agent_runs ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_run_from_row(row) for row in rows]

    async def set_status(self, run_id: str, status: RunStatus) -> AgentRun:
        async with self._write_lock:
            now = datetime.now(UTC)
            cursor = await self._connection.execute(
                """
                UPDATE learnloop_agent_runs SET status = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (status, now.isoformat(), run_id),
            )
            if cursor.rowcount != 1:
                await cursor.close()
                raise LookupError(f"agent run {run_id} was not found")
            await cursor.close()
            await self._connection.commit()
            run = await self.get(run_id)
            if run is None:
                raise LookupError(f"agent run {run_id} was not found")
            return run

    async def append_event(
        self,
        run_id: str,
        event: EventKind,
        *,
        node: str | None = None,
        data: dict[str, object] | None = None,
    ) -> AgentEvent:
        async with self._write_lock:
            cursor = await self._connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1
                FROM learnloop_agent_events WHERE run_id = ?
                """,
                (run_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise RuntimeError("failed to allocate Agent event sequence")
            sequence = int(row[0])
            timestamp = datetime.now(UTC)
            payload = data or {}
            await self._connection.execute(
                """
                INSERT INTO learnloop_agent_events (
                    run_id, sequence, event, node, timestamp, data_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    sequence,
                    event,
                    node,
                    timestamp.isoformat(),
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            await self._connection.commit()
            return AgentEvent(
                run_id=run_id,
                sequence=sequence,
                event=event,
                node=node,
                timestamp=timestamp,
                data=payload,
            )

    async def list_events(
        self, run_id: str, *, after_sequence: int = 0
    ) -> list[AgentEvent]:
        cursor = await self._connection.execute(
            """
            SELECT * FROM learnloop_agent_events
            WHERE run_id = ? AND sequence > ? ORDER BY sequence
            """,
            (run_id, after_sequence),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_event_from_row(row) for row in rows]

    async def start_tool_call(
        self,
        *,
        call_id: str,
        run_id: str,
        tool_name: str,
        arguments: dict[str, object],
        started_at: datetime,
    ) -> None:
        async with self._write_lock:
            await self._connection.execute(
                """
                INSERT OR IGNORE INTO learnloop_tool_calls (
                    call_id, run_id, tool_name, arguments_json, status, started_at
                ) VALUES (?, ?, ?, ?, 'running', ?)
                """,
                (
                    call_id,
                    run_id,
                    tool_name,
                    json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
                    started_at.isoformat(),
                ),
            )
            await self._connection.commit()

    async def finish_tool_call(
        self,
        *,
        call_id: str,
        result_summary: dict[str, object],
        status: str,
        duration_ms: float,
        error: str | None,
        completed_at: datetime,
    ) -> None:
        async with self._write_lock:
            await self._connection.execute(
                """
                UPDATE learnloop_tool_calls
                SET result_summary_json = ?, status = ?, duration_ms = ?,
                    error = ?, completed_at = ?
                WHERE call_id = ?
                """,
                (
                    json.dumps(
                        result_summary, ensure_ascii=False, separators=(",", ":")
                    ),
                    status,
                    duration_ms,
                    error,
                    completed_at.isoformat(),
                    call_id,
                ),
            )
            await self._connection.commit()

    async def list_tool_calls(self, run_id: str) -> list[ToolCallTrace]:
        cursor = await self._connection.execute(
            """
            SELECT * FROM learnloop_tool_calls
            WHERE run_id = ? ORDER BY started_at, call_id
            """,
            (run_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_tool_call_from_row(row) for row in rows]

    async def record_model_call(self, observation: ModelCallObservation) -> None:
        now = datetime.now(UTC)
        call_id = str(uuid4())
        async with self._write_lock:
            await self._connection.execute(
                """
                INSERT OR IGNORE INTO learnloop_prompt_versions (
                    prompt_name, prompt_version, first_seen_at
                ) VALUES (?, ?, ?)
                """,
                (observation.prompt_name, observation.prompt_version, now.isoformat()),
            )
            await self._connection.execute(
                """
                INSERT INTO learnloop_model_calls (
                    call_id, run_id, prompt_name, prompt_version, model,
                    input_tokens, output_tokens, total_tokens, duration_ms,
                    attempts, repaired, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    observation.run_id,
                    observation.prompt_name,
                    observation.prompt_version,
                    observation.model,
                    observation.usage.input_tokens,
                    observation.usage.output_tokens,
                    observation.usage.total_tokens,
                    observation.duration_ms,
                    observation.attempts,
                    int(observation.repaired),
                    observation.error,
                    now.isoformat(),
                ),
            )
            await self._connection.commit()
        if observation.run_id is not None:
            await self.append_event(
                observation.run_id,
                "model_completed",
                node=observation.prompt_name,
                data={
                    "prompt_version": observation.prompt_version,
                    "model": observation.model,
                    "total_tokens": observation.usage.total_tokens,
                    "duration_ms": round(observation.duration_ms, 3),
                    "attempts": observation.attempts,
                    "repaired": observation.repaired,
                    "error": observation.error,
                },
            )

    async def list_model_calls(self, run_id: str) -> list[ModelCallTrace]:
        cursor = await self._connection.execute(
            """
            SELECT * FROM learnloop_model_calls
            WHERE run_id = ? ORDER BY created_at, call_id
            """,
            (run_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_model_call_from_row(row) for row in rows]

    async def list_prompt_versions(self) -> list[dict[str, str]]:
        cursor = await self._connection.execute(
            """
            SELECT prompt_name, prompt_version, first_seen_at
            FROM learnloop_prompt_versions ORDER BY prompt_name, prompt_version
            """
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {
                "prompt_name": str(row["prompt_name"]),
                "prompt_version": str(row["prompt_version"]),
                "first_seen_at": str(row["first_seen_at"]),
            }
            for row in rows
        ]

    async def cleanup_candidates(self, cutoff: datetime) -> list[AgentRun]:
        cursor = await self._connection.execute(
            """
            SELECT * FROM learnloop_agent_runs
            WHERE status IN ('completed', 'failed') AND updated_at < ?
            """,
            (cutoff.astimezone(UTC).isoformat(),),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_run_from_row(row) for row in rows]

    async def delete(self, run_id: str) -> None:
        async with self._write_lock:
            await self._connection.execute(
                "DELETE FROM learnloop_agent_runs WHERE run_id = ?", (run_id,)
            )
            await self._connection.commit()


def _run_from_row(row: aiosqlite.Row) -> AgentRun:
    return AgentRun(
        run_id=str(row["run_id"]),
        thread_id=str(row["thread_id"]),
        graph_kind=str(row["graph_kind"]),  # type: ignore[arg-type]
        resource_id=str(row["resource_id"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _event_from_row(row: aiosqlite.Row) -> AgentEvent:
    decoded = json.loads(str(row["data_json"]))
    if not isinstance(decoded, dict):
        raise ValueError("stored Agent event data must be an object")
    return AgentEvent(
        run_id=str(row["run_id"]),
        sequence=int(row["sequence"]),
        event=str(row["event"]),  # type: ignore[arg-type]
        node=str(row["node"]) if row["node"] is not None else None,
        timestamp=datetime.fromisoformat(str(row["timestamp"])),
        data=decoded,
    )


def _tool_call_from_row(row: aiosqlite.Row) -> ToolCallTrace:
    arguments = json.loads(str(row["arguments_json"]))
    result = (
        json.loads(str(row["result_summary_json"]))
        if row["result_summary_json"] is not None
        else None
    )
    if not isinstance(arguments, dict):
        raise ValueError("stored Tool arguments must be an object")
    if result is not None and not isinstance(result, dict):
        raise ValueError("stored Tool result summary must be an object")
    return ToolCallTrace(
        call_id=str(row["call_id"]),
        run_id=str(row["run_id"]),
        tool_name=str(row["tool_name"]),
        arguments=arguments,
        result_summary=result,
        status=str(row["status"]),
        duration_ms=(
            float(row["duration_ms"]) if row["duration_ms"] is not None else None
        ),
        error=str(row["error"]) if row["error"] is not None else None,
        started_at=datetime.fromisoformat(str(row["started_at"])),
        completed_at=(
            datetime.fromisoformat(str(row["completed_at"]))
            if row["completed_at"] is not None
            else None
        ),
    )


def _model_call_from_row(row: aiosqlite.Row) -> ModelCallTrace:
    return ModelCallTrace(
        call_id=str(row["call_id"]),
        run_id=str(row["run_id"]) if row["run_id"] is not None else None,
        prompt_name=str(row["prompt_name"]),
        prompt_version=str(row["prompt_version"]),
        model=str(row["model"]),
        input_tokens=int(row["input_tokens"]),
        output_tokens=int(row["output_tokens"]),
        total_tokens=int(row["total_tokens"]),
        duration_ms=float(row["duration_ms"]),
        attempts=int(row["attempts"]),
        repaired=bool(row["repaired"]),
        error=str(row["error"]) if row["error"] is not None else None,
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )
