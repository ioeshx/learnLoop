"""Durable JSON Trace store for bounded Research Agent runs."""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite

from app.agent.research.models import ResearchTrace


class SqliteResearchStore:
    """Persist the complete public research trajectory as one atomic snapshot.

    Query, Evidence, Claim, and Citation IDs remain typed inside ``trace_json``.
    The relational envelope supports user/goal/status listing while an atomic JSON
    payload prevents readers from observing a half-written citation graph.
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection
        self._write_lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path) -> SqliteResearchStore:
        connection = await aiosqlite.connect(path.as_posix())
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.execute("PRAGMA busy_timeout = 5000")
        return cls(connection)

    async def close(self) -> None:
        await self._connection.close()

    async def save(self, trace: ResearchTrace) -> None:
        async with self._write_lock:
            await self._connection.execute(
                """
                INSERT INTO research_runs (
                    id, user_id, goal_id, knowledge_node_id, question,
                    retrieval_mode, status, trace_json, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    trace_json = excluded.trace_json,
                    completed_at = excluded.completed_at
                """,
                (
                    trace.id,
                    trace.user_id,
                    trace.request.goal_id,
                    trace.request.knowledge_node_id,
                    trace.request.question,
                    trace.mode.value,
                    trace.status,
                    trace.model_dump_json(),
                    trace.created_at.isoformat(),
                    trace.completed_at.isoformat(),
                ),
            )
            await self._connection.commit()

    async def get(self, trace_id: str) -> ResearchTrace | None:
        cursor = await self._connection.execute(
            "SELECT trace_json FROM research_runs WHERE id = ?", (trace_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return (
            ResearchTrace.model_validate_json(str(row["trace_json"]))
            if row is not None
            else None
        )

    async def list_for_user(
        self, user_id: str, *, goal_id: str | None = None, limit: int = 50
    ) -> list[ResearchTrace]:
        where = "user_id = ?"
        parameters: list[object] = [user_id]
        if goal_id is not None:
            where += " AND goal_id = ?"
            parameters.append(goal_id)
        parameters.append(limit)
        cursor = await self._connection.execute(
            f"""
            SELECT trace_json FROM research_runs
            WHERE {where}
            ORDER BY created_at DESC LIMIT ?
            """,
            parameters,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            ResearchTrace.model_validate_json(str(row["trace_json"])) for row in rows
        ]
