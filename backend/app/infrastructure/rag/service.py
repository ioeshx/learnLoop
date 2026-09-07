"""Synchronous resource ingestion and hybrid retrieval orchestration."""

import io
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from app.application.errors import NotFoundError
from app.application.ports import UnitOfWorkFactory
from app.domain.resources import (
    DocumentChunk,
    LearningResource,
    ResourceCitation,
    ResourceSourceType,
)
from app.infrastructure.database import SqliteResourceStore
from app.infrastructure.embeddings import EmbeddingProvider
from app.infrastructure.parsers import (
    ParsedDocument,
    SafeWebPageFetcher,
    chunk_document,
    parse_document,
)
from app.infrastructure.storage import DocumentStorage


class RagService:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        store: SqliteResourceStore,
        storage: DocumentStorage,
        embeddings: EmbeddingProvider,
        web_fetcher: SafeWebPageFetcher,
        clock: Callable[[], datetime],
        user_id: str,
        chunk_size: int,
        chunk_overlap: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._store = store
        self._storage = storage
        self._embeddings = embeddings
        self._web_fetcher = web_fetcher
        self._clock = clock
        self._user_id = user_id
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    async def ingest_file(
        self,
        source: BinaryIO,
        *,
        filename: str,
        media_type: str,
        goal_id: str,
        knowledge_node_id: str | None = None,
        title: str | None = None,
    ) -> LearningResource:
        await self._validate_scope(goal_id, knowledge_node_id)
        stored = self._storage.save(source)
        duplicate = await self._store.find_duplicate(
            goal_id=goal_id,
            knowledge_node_id=knowledge_node_id,
            sha256=stored.sha256,
        )
        if duplicate is not None:
            return duplicate
        try:
            with self._storage.open(stored.key) as stored_file:
                content = stored_file.read()
            parsed = parse_document(
                content,
                filename=filename,
                media_type=media_type or "application/octet-stream",
            )
        except BaseException:
            if not stored.deduplicated:
                self._storage.recycle(stored.key)
            raise
        resource = LearningResource.create(
            user_id=self._user_id,
            goal_id=goal_id,
            knowledge_node_id=knowledge_node_id,
            title=title or parsed.title or Path(filename).stem,
            source_type=ResourceSourceType.FILE,
            source_uri=None,
            original_filename=filename,
            media_type=media_type or "application/octet-stream",
            storage_key=stored.key,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            now=self._clock(),
        )
        stored_resource = await self._store.add(resource)
        if stored_resource.id != resource.id:
            return stored_resource
        return await self._index(resource, parsed)

    async def ingest_url(
        self,
        url: str,
        *,
        goal_id: str,
        knowledge_node_id: str | None = None,
        title: str | None = None,
    ) -> LearningResource:
        await self._validate_scope(goal_id, knowledge_node_id)
        page = await self._web_fetcher.fetch(url)
        stored = self._storage.save(io.BytesIO(page.content))
        duplicate = await self._store.find_duplicate(
            goal_id=goal_id,
            knowledge_node_id=knowledge_node_id,
            sha256=stored.sha256,
        )
        if duplicate is not None:
            return duplicate
        filename = Path(urlsplit(page.final_url).path).name or "web-page.html"
        try:
            parsed = parse_document(
                page.content,
                filename=filename,
                media_type=page.media_type,
            )
        except BaseException:
            if not stored.deduplicated:
                self._storage.recycle(stored.key)
            raise
        resource = LearningResource.create(
            user_id=self._user_id,
            goal_id=goal_id,
            knowledge_node_id=knowledge_node_id,
            title=title or parsed.title or page.final_url,
            source_type=ResourceSourceType.URL,
            source_uri=page.final_url,
            original_filename=None,
            media_type=page.media_type,
            storage_key=stored.key,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            now=self._clock(),
        )
        stored_resource = await self._store.add(resource)
        if stored_resource.id != resource.id:
            return stored_resource
        return await self._index(resource, parsed)

    async def get(self, resource_id: str) -> LearningResource:
        resource = await self._store.get(resource_id)
        if resource is None:
            raise NotFoundError("learning resource", resource_id)
        return resource

    async def list_resources(
        self,
        *,
        goal_id: str | None = None,
        knowledge_node_id: str | None = None,
    ) -> list[LearningResource]:
        return await self._store.list_resources(
            goal_id=goal_id, knowledge_node_id=knowledge_node_id
        )

    async def delete(self, resource_id: str) -> None:
        resource = await self.get(resource_id)
        references = await self._store.storage_reference_count(resource.storage_key)
        await self._store.delete(resource.id)
        if references <= 1:
            self._storage.recycle(resource.storage_key)

    async def search(
        self,
        query: str,
        *,
        goal_id: str,
        knowledge_node_id: str | None = None,
        limit: int = 5,
    ) -> list[ResourceCitation]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("resource search query must not be empty")
        await self._validate_scope(goal_id, knowledge_node_id)
        embedding = await self._embeddings.embed_query(normalized_query)
        return await self._store.hybrid_search(
            query=normalized_query,
            query_embedding=embedding,
            goal_id=goal_id,
            knowledge_node_id=knowledge_node_id,
            limit=limit,
        )

    async def search_for_knowledge_node(
        self, knowledge_node_id: str, *, limit: int = 5
    ) -> list[ResourceCitation]:
        async with self._uow_factory() as uow:
            node = await uow.knowledge.get_node(knowledge_node_id)
            if node is None:
                raise NotFoundError("knowledge node", knowledge_node_id)
        return await self.search(
            f"{node.title}\n{node.description}",
            goal_id=node.goal_id,
            knowledge_node_id=node.id,
            limit=limit,
        )

    async def close(self) -> None:
        await self._web_fetcher.aclose()
        await self._store.close()

    async def _index(
        self, resource: LearningResource, parsed: ParsedDocument
    ) -> LearningResource:
        try:
            text_chunks = chunk_document(
                parsed,
                chunk_size=self._chunk_size,
                overlap=self._chunk_overlap,
            )
            vectors = await self._embeddings.embed_documents(
                [chunk.content for chunk in text_chunks]
            )
            if len(vectors) != len(text_chunks):
                raise ValueError("embedding provider returned an invalid vector count")
            chunks = [
                DocumentChunk.create(
                    resource_id=resource.id,
                    position=position,
                    content=text_chunk.content,
                    token_count=text_chunk.token_count,
                    page_number=text_chunk.page_number,
                    section=text_chunk.section,
                    embedding=vector,
                    embedding_model=self._embeddings.model_name,
                )
                for position, (text_chunk, vector) in enumerate(
                    zip(text_chunks, vectors, strict=True)
                )
            ]
            await self._store.finish(resource.id, chunks)
        except BaseException as error:
            await self._store.fail(resource.id, str(error))
            raise
        indexed = await self._store.get(resource.id)
        if indexed is None:
            raise RuntimeError("indexed learning resource disappeared")
        return indexed

    async def _validate_scope(
        self, goal_id: str, knowledge_node_id: str | None
    ) -> None:
        async with self._uow_factory() as uow:
            goal = await uow.goals.get(goal_id)
            if goal is None:
                raise NotFoundError("learning goal", goal_id)
            if knowledge_node_id is None:
                return
            node = await uow.knowledge.get_node(knowledge_node_id)
            if node is None:
                raise NotFoundError("knowledge node", knowledge_node_id)
            if node.goal_id != goal.id:
                raise ValueError("knowledge node does not belong to the learning goal")
