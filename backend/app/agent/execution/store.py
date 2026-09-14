"""SQLite registry for thread mappings and replayable SSE events."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import aiosqlite

from app.agent.execution.models import (
    AgentEvent,
    AgentRun,
    EngineVersion,
    EventKind,
    GraphKind,
    ModelCallTrace,
    RunStatus,
    TerminalReason,
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
        await self._upgrade_legacy_run_table()
        await self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS learnloop_agent_runs (
                run_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL UNIQUE,
                graph_kind TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                engine_version TEXT NOT NULL DEFAULT 'fixed_v1',
                parent_run_id TEXT,
                attempt_no INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL,
                terminal_reason TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (parent_run_id) REFERENCES learnloop_agent_runs(run_id)
                    ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS ix_learnloop_agent_runs_resource_attempt
                ON learnloop_agent_runs
                (graph_kind, resource_id, engine_version, attempt_no);
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
            CREATE TABLE IF NOT EXISTS learnloop_dynamic_agent_states (
                run_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES learnloop_agent_runs(run_id)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS learnloop_agent_plan_versions (
                run_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                plan_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (run_id, version),
                FOREIGN KEY (run_id) REFERENCES learnloop_agent_runs(run_id)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS learnloop_context_artifacts (
                artifact_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                version INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                content_json TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                UNIQUE (run_id, kind, sha256),
                FOREIGN KEY (run_id) REFERENCES learnloop_agent_runs(run_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS ix_learnloop_context_artifacts_run_created
                ON learnloop_context_artifacts (run_id, created_at);
            CREATE TABLE IF NOT EXISTS learnloop_context_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                purpose TEXT NOT NULL,
                plan_version INTEGER NOT NULL,
                step_id TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                context_json TEXT,
                observed_model_input_tokens INTEGER,
                token_delta INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES learnloop_agent_runs(run_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS ix_learnloop_context_snapshots_run_created
                ON learnloop_context_snapshots (run_id, created_at);
            """
        )
        await self._connection.commit()

    async def _upgrade_legacy_run_table(self) -> None:
        """将 v1 单 Run 表原地升级为可重复运行的 v2 Run protocol。

        SQLite 无法直接删除 ``UNIQUE(graph_kind, resource_id)``，所以这里使用 table
        rebuild。``legacy_alter_table`` 保证已有 Event/Trace foreign key 仍指向新表名；
        迁移全程在 transaction 中完成，失败时不会留下半张表。
        """

        cursor = await self._connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='learnloop_agent_runs'"
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return
        sql = str(row["sql"])
        if "engine_version" in sql and "UNIQUE (graph_kind, resource_id)" not in sql:
            return
        await self._connection.commit()
        await self._connection.execute("PRAGMA foreign_keys = OFF")
        await self._connection.execute("PRAGMA legacy_alter_table = ON")
        try:
            await self._connection.executescript(
                """
                BEGIN IMMEDIATE;
                ALTER TABLE learnloop_agent_runs RENAME TO learnloop_agent_runs_v1;
                CREATE TABLE learnloop_agent_runs (
                    run_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL UNIQUE,
                    graph_kind TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    engine_version TEXT NOT NULL DEFAULT 'fixed_v1',
                    parent_run_id TEXT,
                    attempt_no INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL,
                    terminal_reason TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (parent_run_id) REFERENCES learnloop_agent_runs(run_id)
                        ON DELETE SET NULL
                );
                INSERT INTO learnloop_agent_runs (
                    run_id, thread_id, graph_kind, resource_id, engine_version,
                    parent_run_id, attempt_no, status, terminal_reason,
                    cancel_requested, version, created_at, updated_at
                )
                SELECT run_id, thread_id, graph_kind, resource_id, 'fixed_v1',
                       NULL, 1, status,
                       CASE status
                           WHEN 'completed' THEN 'completed'
                           WHEN 'failed' THEN 'failed'
                           ELSE NULL
                       END,
                       0, 1, created_at, updated_at
                FROM learnloop_agent_runs_v1;
                DROP TABLE learnloop_agent_runs_v1;
                COMMIT;
                """
            )
        finally:
            await self._connection.execute("PRAGMA legacy_alter_table = OFF")
            await self._connection.execute("PRAGMA foreign_keys = ON")

    async def close(self) -> None:
        await self._connection.close()

    async def create_or_get(
        self,
        graph_kind: GraphKind,
        resource_id: str,
        *,
        engine_version: EngineVersion = "fixed_v1",
        parent_run_id: str | None = None,
    ) -> tuple[AgentRun, bool]:
        """创建独立 Run；名称为兼容 v1 caller 保留，返回值始终是新 Run。"""

        async with self._write_lock:
            cursor = await self._connection.execute(
                """
                SELECT COALESCE(MAX(attempt_no), 0) + 1
                FROM learnloop_agent_runs
                WHERE graph_kind = ? AND resource_id = ? AND engine_version = ?
                """,
                (graph_kind, resource_id, engine_version),
            )
            row = await cursor.fetchone()
            await cursor.close()
            attempt_no = int(row[0]) if row is not None else 1
            now = datetime.now(UTC)
            run = AgentRun(
                run_id=str(uuid4()),
                thread_id=str(uuid4()),
                graph_kind=graph_kind,
                resource_id=resource_id,
                engine_version=engine_version,
                parent_run_id=parent_run_id,
                attempt_no=attempt_no,
                status="created",
                terminal_reason=None,
                cancel_requested=False,
                version=1,
                created_at=now,
                updated_at=now,
            )
            await self._connection.execute(
                """
                INSERT INTO learnloop_agent_runs (
                    run_id, thread_id, graph_kind, resource_id, engine_version,
                    parent_run_id, attempt_no, status, terminal_reason,
                    cancel_requested, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.thread_id,
                    run.graph_kind,
                    run.resource_id,
                    run.engine_version,
                    run.parent_run_id,
                    run.attempt_no,
                    run.status,
                    run.terminal_reason,
                    int(run.cancel_requested),
                    run.version,
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

    async def set_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        terminal_reason: TerminalReason | None = None,
        expected_version: int | None = None,
    ) -> AgentRun:
        async with self._write_lock:
            existing = await self.get(run_id)
            if existing is None:
                raise LookupError(f"agent run {run_id} was not found")
            _validate_status_transition(existing.status, status)
            if expected_version is not None and existing.version != expected_version:
                raise RuntimeError("agent run was modified concurrently")
            if status in {"completed", "failed", "cancelled"}:
                terminal_reason = terminal_reason or status
            elif terminal_reason is not None:
                raise ValueError("only terminal Run states may have terminal_reason")
            now = datetime.now(UTC)
            cursor = await self._connection.execute(
                """
                UPDATE learnloop_agent_runs
                SET status = ?, terminal_reason = ?, updated_at = ?,
                    version = version + 1
                WHERE run_id = ? AND version = ?
                """,
                (
                    status,
                    terminal_reason,
                    now.isoformat(),
                    run_id,
                    existing.version,
                ),
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

    async def request_cancel(self, run_id: str) -> AgentRun:
        async with self._write_lock:
            existing = await self.get(run_id)
            if existing is None:
                raise LookupError(f"agent run {run_id} was not found")
            if existing.status in {"completed", "failed", "cancelled"}:
                return existing
            now = datetime.now(UTC)
            await self._connection.execute(
                """
                UPDATE learnloop_agent_runs
                SET cancel_requested = 1, updated_at = ?, version = version + 1
                WHERE run_id = ?
                """,
                (now.isoformat(), run_id),
            )
            await self._connection.commit()
            refreshed = await self.get(run_id)
            if refreshed is None:
                raise LookupError(f"agent run {run_id} was not found")
            return refreshed

    async def save_dynamic_state(self, run_id: str, state_json: str) -> None:
        async with self._write_lock:
            await self._connection.execute(
                """
                INSERT INTO learnloop_dynamic_agent_states
                    (run_id, state_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (run_id, state_json, datetime.now(UTC).isoformat()),
            )
            await self._connection.commit()

    async def load_dynamic_state(self, run_id: str) -> str | None:
        cursor = await self._connection.execute(
            "SELECT state_json FROM learnloop_dynamic_agent_states WHERE run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return str(row["state_json"]) if row is not None else None

    async def save_plan_version(
        self, run_id: str, version: int, plan_json: str
    ) -> None:
        async with self._write_lock:
            await self._connection.execute(
                """
                INSERT OR IGNORE INTO learnloop_agent_plan_versions
                    (run_id, version, plan_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, version, plan_json, datetime.now(UTC).isoformat()),
            )
            await self._connection.commit()

    async def list_plan_versions(self, run_id: str) -> list[dict[str, object]]:
        cursor = await self._connection.execute(
            """
            SELECT version, plan_json, created_at
            FROM learnloop_agent_plan_versions
            WHERE run_id = ? ORDER BY version
            """,
            (run_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {
                "version": int(row["version"]),
                "plan": json.loads(str(row["plan_json"])),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    async def save_context_artifact(
        self,
        run_id: str,
        *,
        kind: str,
        content: object,
        version: int = 1,
        expires_at: datetime | None = None,
    ) -> dict[str, object]:
        """Persist immutable Context material and return a versioned Artifact ref.

        ``run/kind/hash`` deduplication makes crash replay safe. The full bounded Tool
        result lives here; Agent state and Trace retain only the returned provenance.
        """

        serialized = json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        async with self._write_lock:
            cursor = await self._connection.execute(
                """
                SELECT artifact_id, version, sha256, created_at, expires_at
                FROM learnloop_context_artifacts
                WHERE run_id = ? AND kind = ? AND sha256 = ?
                """,
                (run_id, kind, digest),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                artifact_id = str(uuid4())
                created_at = datetime.now(UTC)
                await self._connection.execute(
                    """
                    INSERT INTO learnloop_context_artifacts (
                        artifact_id, run_id, kind, version, sha256, content_json,
                        size_bytes, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        run_id,
                        kind,
                        version,
                        digest,
                        serialized,
                        len(serialized.encode()),
                        created_at.isoformat(),
                        expires_at.astimezone(UTC).isoformat()
                        if expires_at is not None
                        else None,
                    ),
                )
                await self._connection.commit()
                return {
                    "artifact_id": artifact_id,
                    "kind": kind,
                    "version": version,
                    "sha256": digest,
                    "created_at": created_at.isoformat(),
                    "expires_at": expires_at.astimezone(UTC).isoformat()
                    if expires_at is not None
                    else None,
                }
            return {
                "artifact_id": str(row["artifact_id"]),
                "kind": kind,
                "version": int(row["version"]),
                "sha256": str(row["sha256"]),
                "created_at": str(row["created_at"]),
                "expires_at": (
                    str(row["expires_at"])
                    if row["expires_at"] is not None
                    else None
                ),
            }

    async def get_context_artifact(
        self, artifact_id: str
    ) -> dict[str, object] | None:
        cursor = await self._connection.execute(
            "SELECT * FROM learnloop_context_artifacts WHERE artifact_id = ?",
            (artifact_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return {
            "artifact_id": str(row["artifact_id"]),
            "run_id": str(row["run_id"]),
            "kind": str(row["kind"]),
            "version": int(row["version"]),
            "sha256": str(row["sha256"]),
            "content": json.loads(str(row["content_json"])),
            "size_bytes": int(row["size_bytes"]),
            "created_at": str(row["created_at"]),
            "expires_at": (
                str(row["expires_at"])
                if row["expires_at"] is not None
                else None
            ),
        }

    async def save_context_snapshot(
        self,
        metadata: dict[str, object],
        *,
        context_values: dict[str, object] | None = None,
    ) -> None:
        """Store provenance by default and sensitive Context only in debug mode."""

        async with self._write_lock:
            await self._connection.execute(
                """
                INSERT INTO learnloop_context_snapshots (
                    snapshot_id, run_id, purpose, plan_version, step_id,
                    metadata_json, context_json, observed_model_input_tokens,
                    token_delta, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(metadata["snapshot_id"]),
                    str(metadata["run_id"]),
                    str(metadata["purpose"]),
                    int(str(metadata["plan_version"])),
                    str(metadata["step_id"]),
                    json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                    (
                        json.dumps(
                            context_values,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        if context_values is not None
                        else None
                    ),
                    metadata.get("observed_model_input_tokens"),
                    metadata.get("token_delta"),
                    str(metadata["created_at"]),
                ),
            )
            await self._connection.commit()

    async def record_context_token_observation(
        self, snapshot_id: str, actual_input_tokens: int
    ) -> None:
        """Calibrate estimation against provider usage without exposing Prompt text."""

        async with self._write_lock:
            cursor = await self._connection.execute(
                "SELECT metadata_json FROM learnloop_context_snapshots "
                "WHERE snapshot_id = ?",
                (snapshot_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise LookupError(f"Context snapshot {snapshot_id} was not found")
            metadata = json.loads(str(row["metadata_json"]))
            estimated = int(metadata["total_input_tokens"])
            token_delta = actual_input_tokens - estimated
            metadata["observed_model_input_tokens"] = actual_input_tokens
            metadata["token_delta"] = token_delta
            await self._connection.execute(
                """
                UPDATE learnloop_context_snapshots
                SET metadata_json = ?, observed_model_input_tokens = ?, token_delta = ?
                WHERE snapshot_id = ?
                """,
                (
                    json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                    actual_input_tokens,
                    token_delta,
                    snapshot_id,
                ),
            )
            await self._connection.commit()

    async def list_context_snapshots(
        self, run_id: str, *, include_content: bool = False
    ) -> list[dict[str, object]]:
        cursor = await self._connection.execute(
            """
            SELECT metadata_json, context_json
            FROM learnloop_context_snapshots
            WHERE run_id = ? ORDER BY created_at, snapshot_id
            """,
            (run_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        snapshots: list[dict[str, object]] = []
        for row in rows:
            metadata = json.loads(str(row["metadata_json"]))
            if include_content and row["context_json"] is not None:
                metadata["context"] = json.loads(str(row["context_json"]))
            snapshots.append(metadata)
        return snapshots

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
            WHERE status IN ('completed', 'failed', 'cancelled') AND updated_at < ?
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
        engine_version=str(row["engine_version"]),  # type: ignore[arg-type]
        parent_run_id=(
            str(row["parent_run_id"]) if row["parent_run_id"] is not None else None
        ),
        attempt_no=int(row["attempt_no"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        terminal_reason=cast(
            TerminalReason | None,
            (
                str(row["terminal_reason"])
                if row["terminal_reason"] is not None
                else None
            ),
        ),
        cancel_requested=bool(row["cancel_requested"]),
        version=int(row["version"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _validate_status_transition(current: RunStatus, target: RunStatus) -> None:
    allowed: dict[RunStatus, set[RunStatus]] = {
        "created": {"created", "running", "cancelled", "failed"},
        "running": {"running", "awaiting_input", "completed", "failed", "cancelled"},
        "awaiting_input": {"awaiting_input", "running", "cancelled", "failed"},
        "completed": {"completed"},
        "failed": {"failed"},
        "cancelled": {"cancelled"},
    }
    if target not in allowed[current]:
        raise ValueError(f"invalid Agent Run transition: {current} -> {target}")


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
