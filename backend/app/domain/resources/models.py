"""Resource and citation entities for local-first retrieval."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4


class ResourceSourceType(StrEnum):
    FILE = "file"
    URL = "url"


class ResourceStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class LearningResource:
    id: str
    user_id: str
    goal_id: str
    knowledge_node_id: str | None
    title: str
    source_type: ResourceSourceType
    source_uri: str | None
    original_filename: str | None
    media_type: str
    storage_key: str
    sha256: str
    size_bytes: int
    status: ResourceStatus
    error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        user_id: str,
        goal_id: str,
        knowledge_node_id: str | None,
        title: str,
        source_type: ResourceSourceType,
        source_uri: str | None,
        original_filename: str | None,
        media_type: str,
        storage_key: str,
        sha256: str,
        size_bytes: int,
        now: datetime,
    ) -> "LearningResource":
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("resource title must not be empty")
        if size_bytes <= 0:
            raise ValueError("resource must not be empty")
        return cls(
            id=str(uuid4()),
            user_id=user_id,
            goal_id=goal_id,
            knowledge_node_id=knowledge_node_id,
            title=normalized_title,
            source_type=source_type,
            source_uri=source_uri,
            original_filename=(
                Path(original_filename).name if original_filename else None
            ),
            media_type=media_type,
            storage_key=storage_key,
            sha256=sha256,
            size_bytes=size_bytes,
            status=ResourceStatus.PROCESSING,
            error=None,
            created_at=now,
            updated_at=now,
        )


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    id: str
    resource_id: str
    position: int
    content: str
    token_count: int
    page_number: int | None
    section: str | None
    embedding: tuple[float, ...]
    embedding_model: str

    @classmethod
    def create(
        cls,
        *,
        resource_id: str,
        position: int,
        content: str,
        token_count: int,
        page_number: int | None,
        section: str | None,
        embedding: list[float],
        embedding_model: str,
    ) -> "DocumentChunk":
        normalized = content.strip()
        if not normalized:
            raise ValueError("document chunk must not be empty")
        if position < 0 or token_count <= 0:
            raise ValueError("invalid document chunk position or token count")
        if not embedding:
            raise ValueError("document chunk embedding must not be empty")
        return cls(
            id=str(uuid4()),
            resource_id=resource_id,
            position=position,
            content=normalized,
            token_count=token_count,
            page_number=page_number,
            section=section.strip() if section else None,
            embedding=tuple(float(value) for value in embedding),
            embedding_model=embedding_model,
        )


@dataclass(frozen=True, slots=True)
class ResourceCitation:
    resource_id: str
    chunk_id: str
    title: str
    excerpt: str
    score: float
    page_number: int | None
    section: str | None
    source_uri: str | None

    @property
    def locator(self) -> str | None:
        if self.page_number is not None:
            return f"第 {self.page_number} 页"
        return self.section
