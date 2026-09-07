"""SQLite resource persistence, FTS5 ranking, and vector retrieval."""

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import numpy as np
import numpy.typing as npt

from app.domain.resources import (
    DocumentChunk,
    LearningResource,
    ResourceCitation,
    ResourceSourceType,
    ResourceStatus,
)


class SqliteResourceStore:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection
        self._write_lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path) -> "SqliteResourceStore":
        connection = await aiosqlite.connect(path.as_posix())
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.execute("PRAGMA busy_timeout = 5000")
        return cls(connection)

    async def close(self) -> None:
        await self._connection.close()

    async def add(self, resource: LearningResource) -> LearningResource:
        async with self._write_lock:
            try:
                await self._connection.execute(
                    """
                    INSERT INTO learning_resources (
                        id, user_id, goal_id, knowledge_node_id, title, source_type,
                        source_uri, original_filename, media_type, storage_key,
                        sha256, size_bytes, status, error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resource.id,
                        resource.user_id,
                        resource.goal_id,
                        resource.knowledge_node_id,
                        resource.title,
                        resource.source_type.value,
                        resource.source_uri,
                        resource.original_filename,
                        resource.media_type,
                        resource.storage_key,
                        resource.sha256,
                        resource.size_bytes,
                        resource.status.value,
                        resource.error,
                        resource.created_at.isoformat(),
                        resource.updated_at.isoformat(),
                    ),
                )
                await self._connection.commit()
                return resource
            except aiosqlite.IntegrityError:
                await self._connection.rollback()
                duplicate = await self.find_duplicate(
                    goal_id=resource.goal_id,
                    knowledge_node_id=resource.knowledge_node_id,
                    sha256=resource.sha256,
                )
                if duplicate is not None:
                    return duplicate
                raise

    async def finish(self, resource_id: str, chunks: list[DocumentChunk]) -> None:
        if not chunks:
            raise ValueError("a ready resource requires at least one chunk")
        async with self._write_lock:
            try:
                await self._connection.execute("BEGIN IMMEDIATE")
                await self._connection.executemany(
                    """
                    INSERT INTO document_chunks (
                        id, resource_id, position, content, token_count,
                        page_number, section, embedding, embedding_model
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            chunk.id,
                            chunk.resource_id,
                            chunk.position,
                            chunk.content,
                            chunk.token_count,
                            chunk.page_number,
                            chunk.section,
                            json.dumps(chunk.embedding, separators=(",", ":")),
                            chunk.embedding_model,
                        )
                        for chunk in chunks
                    ],
                )
                cursor = await self._connection.execute(
                    """
                    UPDATE learning_resources
                    SET status = 'ready', error = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (datetime.now(UTC).isoformat(), resource_id),
                )
                if cursor.rowcount != 1:
                    raise LookupError(f"learning resource {resource_id} was not found")
                await cursor.close()
                await self._connection.commit()
            except BaseException:
                await self._connection.rollback()
                raise

    async def fail(self, resource_id: str, message: str) -> None:
        async with self._write_lock:
            await self._connection.execute(
                """
                UPDATE learning_resources
                SET status = 'failed', error = ?, updated_at = ? WHERE id = ?
                """,
                (message[:4000], datetime.now(UTC).isoformat(), resource_id),
            )
            await self._connection.commit()

    async def get(self, resource_id: str) -> LearningResource | None:
        cursor = await self._connection.execute(
            "SELECT * FROM learning_resources WHERE id = ?", (resource_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return _resource_from_row(row) if row is not None else None

    async def find_duplicate(
        self,
        *,
        goal_id: str,
        knowledge_node_id: str | None,
        sha256: str,
    ) -> LearningResource | None:
        cursor = await self._connection.execute(
            """
            SELECT * FROM learning_resources
            WHERE goal_id = ? AND knowledge_node_id IS ? AND sha256 = ?
              AND status IN ('processing', 'ready')
            ORDER BY created_at LIMIT 1
            """,
            (goal_id, knowledge_node_id, sha256),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return _resource_from_row(row) if row is not None else None

    async def list_resources(
        self,
        *,
        goal_id: str | None = None,
        knowledge_node_id: str | None = None,
    ) -> list[LearningResource]:
        clauses: list[str] = []
        parameters: list[object] = []
        if goal_id is not None:
            clauses.append("goal_id = ?")
            parameters.append(goal_id)
        if knowledge_node_id is not None:
            clauses.append("knowledge_node_id = ?")
            parameters.append(knowledge_node_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self._connection.execute(
            f"SELECT * FROM learning_resources {where} ORDER BY created_at DESC",
            parameters,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_resource_from_row(row) for row in rows]

    async def delete(self, resource_id: str) -> None:
        async with self._write_lock:
            await self._connection.execute(
                "DELETE FROM learning_resources WHERE id = ?", (resource_id,)
            )
            await self._connection.commit()

    async def storage_reference_count(self, storage_key: str) -> int:
        cursor = await self._connection.execute(
            "SELECT COUNT(*) FROM learning_resources WHERE storage_key = ?",
            (storage_key,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return int(row[0]) if row is not None else 0

    async def hybrid_search(
        self,
        *,
        query: str,
        query_embedding: list[float],
        goal_id: str,
        knowledge_node_id: str | None,
        limit: int,
    ) -> list[ResourceCitation]:
        candidate_limit = max(limit * 4, 20)
        lexical = await self._lexical_search(
            query=query,
            goal_id=goal_id,
            knowledge_node_id=knowledge_node_id,
            limit=candidate_limit,
        )
        vector = await self._vector_search(
            query_embedding=query_embedding,
            goal_id=goal_id,
            knowledge_node_id=knowledge_node_id,
            limit=candidate_limit,
        )
        scores: dict[str, float] = {}
        rows: dict[str, aiosqlite.Row] = {}
        for ranking in (lexical, vector):
            for rank, row in enumerate(ranking, start=1):
                chunk_id = str(row["chunk_id"])
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (60 + rank)
                rows[chunk_id] = row
        ranked = sorted(scores, key=scores.__getitem__, reverse=True)[:limit]
        return [
            _citation_from_row(rows[chunk_id], scores[chunk_id])
            for chunk_id in ranked
        ]

    async def _lexical_search(
        self,
        *,
        query: str,
        goal_id: str,
        knowledge_node_id: str | None,
        limit: int,
    ) -> list[aiosqlite.Row]:
        fts_query = _fts_query(query)
        if not fts_query:
            return []
        node_clause = (
            "AND (r.knowledge_node_id = ? OR r.knowledge_node_id IS NULL)"
            if knowledge_node_id is not None
            else ""
        )
        parameters: list[object] = [fts_query, goal_id]
        if knowledge_node_id is not None:
            parameters.append(knowledge_node_id)
        parameters.append(limit)
        cursor = await self._connection.execute(
            f"""
            SELECT c.id AS chunk_id, c.content, c.page_number, c.section,
                   r.id AS resource_id, r.title, r.source_uri,
                   bm25(document_chunks_fts, 1.0, 2.0) AS lexical_score,
                   c.embedding
            FROM document_chunks_fts
            JOIN document_chunks c ON c.rowid = document_chunks_fts.rowid
            JOIN learning_resources r ON r.id = c.resource_id
            WHERE document_chunks_fts MATCH ? AND r.goal_id = ?
              AND r.status = 'ready' {node_clause}
            ORDER BY lexical_score
            LIMIT ?
            """,
            parameters,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return list(rows)

    async def _vector_search(
        self,
        *,
        query_embedding: list[float],
        goal_id: str,
        knowledge_node_id: str | None,
        limit: int,
    ) -> list[aiosqlite.Row]:
        node_clause = (
            "AND (r.knowledge_node_id = ? OR r.knowledge_node_id IS NULL)"
            if knowledge_node_id is not None
            else ""
        )
        parameters: list[object] = [goal_id]
        if knowledge_node_id is not None:
            parameters.append(knowledge_node_id)
        cursor = await self._connection.execute(
            f"""
            SELECT c.id AS chunk_id, c.content, c.page_number, c.section,
                   r.id AS resource_id, r.title, r.source_uri, c.embedding
            FROM document_chunks c
            JOIN learning_resources r ON r.id = c.resource_id
            WHERE r.goal_id = ? AND r.status = 'ready' {node_clause}
            """,
            parameters,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        query_vector: npt.NDArray[np.float64] = np.asarray(
            query_embedding, dtype=np.float64
        )
        query_norm = float(np.linalg.norm(query_vector))
        if not query_norm:
            return []
        scored: list[tuple[float, aiosqlite.Row]] = []
        for row in rows:
            raw_embedding = json.loads(str(row["embedding"]))
            vector: npt.NDArray[np.float64] = np.asarray(
                raw_embedding, dtype=np.float64
            )
            if vector.shape != query_vector.shape:
                continue
            denominator = query_norm * float(np.linalg.norm(vector))
            similarity = (
                float(np.dot(query_vector, vector) / denominator)
                if denominator
                else 0
            )
            if similarity >= 0.15:
                scored.append((similarity, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [row for _, row in scored[:limit]]


def _resource_from_row(row: aiosqlite.Row) -> LearningResource:
    return LearningResource(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        goal_id=str(row["goal_id"]),
        knowledge_node_id=(
            str(row["knowledge_node_id"])
            if row["knowledge_node_id"] is not None
            else None
        ),
        title=str(row["title"]),
        source_type=ResourceSourceType(str(row["source_type"])),
        source_uri=str(row["source_uri"]) if row["source_uri"] is not None else None,
        original_filename=(
            str(row["original_filename"])
            if row["original_filename"] is not None
            else None
        ),
        media_type=str(row["media_type"]),
        storage_key=str(row["storage_key"]),
        sha256=str(row["sha256"]),
        size_bytes=int(row["size_bytes"]),
        status=ResourceStatus(str(row["status"])),
        error=str(row["error"]) if row["error"] is not None else None,
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _citation_from_row(row: aiosqlite.Row, score: float) -> ResourceCitation:
    content = str(row["content"])
    excerpt = content if len(content) <= 600 else f"{content[:597]}..."
    return ResourceCitation(
        resource_id=str(row["resource_id"]),
        chunk_id=str(row["chunk_id"]),
        title=str(row["title"]),
        excerpt=excerpt,
        score=score,
        page_number=int(row["page_number"]) if row["page_number"] else None,
        section=str(row["section"]) if row["section"] else None,
        source_uri=str(row["source_uri"]) if row["source_uri"] else None,
    )


def _fts_query(query: str) -> str:
    terms = re.findall(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+", query.lower())
    unique_terms = list(dict.fromkeys(term for term in terms if term.strip()))[:20]
    escaped = [term.replace('"', '""') for term in unique_terms]
    return " OR ".join(f'"{term}"' for term in escaped)
