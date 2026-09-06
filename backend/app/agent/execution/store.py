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
    RunStatus,
)


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
