"""Construct and clean up the standalone worker process."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.application import ApplicationDependencies
from app.application.services import DEFAULT_USER_ID
from app.config import Settings
from app.domain.common import utc_now
from app.infrastructure.database import SqlAlchemyUnitOfWork, SqliteResourceStore
from app.infrastructure.database.engine import create_database
from app.infrastructure.embeddings import (
    EmbeddingProvider,
    LocalHashEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)
from app.infrastructure.parsers import SafeWebPageFetcher
from app.infrastructure.rag import RagService
from app.infrastructure.review import FsrsReviewScheduler
from app.infrastructure.storage import LocalDocumentStorage
from app.workers.handlers import (
    DueReviewGenerationHandler,
    ResourceProcessingHandler,
    WeeklyReportHandler,
)
from app.workers.models import JobType
from app.workers.registry import JobHandlerRegistry
from app.workers.runtime import BackgroundWorker
from app.workers.store import SqliteJobStore


@asynccontextmanager
async def open_background_worker(
    settings: Settings,
) -> AsyncIterator[BackgroundWorker]:
    """组装独立 Worker 所需资源，并在退出时按依赖顺序释放。

    启动时创建数据库、资料存储、Embedding、RAG 服务和任务 Store，随后显式注册三类
    Handler；上下文结束时关闭网络客户端、SQLite 连接和 SQLAlchemy Engine。
    """

    database = create_database(settings)
    resource_store: SqliteResourceStore | None = None
    job_store: SqliteJobStore | None = None
    remote_embedding: OpenAICompatibleEmbeddingProvider | None = None
    rag_service: RagService | None = None
    try:
        def uow_factory() -> SqlAlchemyUnitOfWork:
            """为每次后台领域操作创建独立事务边界。"""

            return SqlAlchemyUnitOfWork(database.session_factory)

        resource_store = await SqliteResourceStore.open(settings.database_path)
        job_store = await SqliteJobStore.open(settings.database_path)
        if settings.embedding_provider == "openai_compatible":
            api_key = settings.embedding_api_key
            if api_key is None:
                raise RuntimeError("validated embedding API key is missing")
            remote_embedding = OpenAICompatibleEmbeddingProvider(
                api_key=api_key.get_secret_value(),
                model=settings.embedding_model,
                base_url=settings.embedding_base_url,
                dimensions=settings.embedding_dimensions,
                timeout_seconds=settings.llm_timeout_seconds,
            )
            embeddings: EmbeddingProvider = remote_embedding
        else:
            embeddings = LocalHashEmbeddingProvider(
                dimensions=settings.embedding_dimensions
            )
        rag_service = RagService(
            uow_factory=uow_factory,
            store=resource_store,
            storage=LocalDocumentStorage(
                settings.document_storage_path,
                max_bytes=settings.resource_max_bytes,
            ),
            embeddings=embeddings,
            web_fetcher=SafeWebPageFetcher(
                max_bytes=settings.resource_max_bytes,
                timeout_seconds=settings.web_fetch_timeout_seconds,
            ),
            clock=utc_now,
            user_id=DEFAULT_USER_ID,
            chunk_size=settings.resource_chunk_size,
            chunk_overlap=settings.resource_chunk_overlap,
        )
        dependencies = ApplicationDependencies(
            uow_factory=uow_factory,
            review_scheduler=FsrsReviewScheduler(),
            resource_search=rag_service,
        )
        registry = JobHandlerRegistry()
        registry.register(
            JobType.RESOURCE_PROCESS, ResourceProcessingHandler(rag_service)
        )
        registry.register(
            JobType.WEEKLY_REPORT,
            WeeklyReportHandler(settings.database_path, utc_now),
        )
        registry.register(
            JobType.DUE_REVIEWS, DueReviewGenerationHandler(dependencies)
        )
        yield BackgroundWorker(
            store=job_store,
            registry=registry,
            clock=utc_now,
            poll_interval_seconds=settings.worker_poll_interval_seconds,
            lease_seconds=settings.worker_lease_seconds,
            retry_base_seconds=settings.job_retry_base_seconds,
            retry_max_seconds=settings.job_retry_max_seconds,
        )
    finally:
        if rag_service is not None:
            await rag_service.close()
        elif resource_store is not None:
            await resource_store.close()
        if job_store is not None:
            await job_store.close()
        if remote_embedding is not None:
            await remote_embedding.aclose()
        await database.dispose()
