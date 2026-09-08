"""LearnLoop API application entry point."""

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.agent.execution import open_agent_runtime
from app.api.router import api_router
from app.application import ApplicationDependencies
from app.application.services import DEFAULT_USER_ID
from app.config import Settings, get_settings
from app.domain.common import utc_now
from app.errors import register_error_handlers
from app.infrastructure.database import (
    SqlAlchemyUnitOfWork,
    SqliteResourceStore,
    create_database,
)
from app.infrastructure.embeddings import (
    EmbeddingProvider,
    LocalHashEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)
from app.infrastructure.llm import (
    DeepSeekModelProvider,
    LlmCurriculumGenerator,
    LlmReviewExerciseGenerator,
    ModelProvider,
    StructuredModel,
)
from app.infrastructure.parsers import SafeWebPageFetcher
from app.infrastructure.rag import RagService
from app.infrastructure.review import FsrsReviewScheduler
from app.infrastructure.storage import LocalDocumentStorage
from app.logging import configure_logging
from app.workers import JobService, SqliteJobStore

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application with explicit, testable settings."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(lifespan_app: FastAPI) -> AsyncGenerator[None, None]:
        database = create_database(resolved_settings)
        model_provider: ModelProvider | None = None
        structured_model: StructuredModel | None = None
        remote_embedding: OpenAICompatibleEmbeddingProvider | None = None
        resource_store: SqliteResourceStore | None = None
        job_store: SqliteJobStore | None = None
        rag_service: RagService | None = None
        curriculum_generator = None
        if resolved_settings.llm_provider == "deepseek":
            api_key = resolved_settings.llm_api_key
            if api_key is None:
                raise RuntimeError("validated DeepSeek API key is missing")
            model_provider = DeepSeekModelProvider(
                api_key=api_key.get_secret_value(),
                model=resolved_settings.llm_model,
                base_url=resolved_settings.llm_base_url,
                timeout_seconds=resolved_settings.llm_timeout_seconds,
                max_retries=resolved_settings.llm_max_retries,
            )
            structured_model = StructuredModel(model_provider)
            curriculum_generator = LlmCurriculumGenerator(structured_model)
        try:
            lifespan_app.state.database = database
            lifespan_app.state.model_provider = model_provider

            def uow_factory() -> SqlAlchemyUnitOfWork:
                return SqlAlchemyUnitOfWork(database.session_factory)

            resource_store = await SqliteResourceStore.open(
                resolved_settings.database_path
            )
            job_store = await SqliteJobStore.open(resolved_settings.database_path)
            if resolved_settings.embedding_provider == "openai_compatible":
                embedding_api_key = resolved_settings.embedding_api_key
                if embedding_api_key is None:
                    raise RuntimeError("validated embedding API key is missing")
                remote_embedding = OpenAICompatibleEmbeddingProvider(
                    api_key=embedding_api_key.get_secret_value(),
                    model=resolved_settings.embedding_model,
                    base_url=resolved_settings.embedding_base_url,
                    dimensions=resolved_settings.embedding_dimensions,
                    timeout_seconds=resolved_settings.llm_timeout_seconds,
                )
                embedding_provider: EmbeddingProvider = remote_embedding
            else:
                embedding_provider = LocalHashEmbeddingProvider(
                    dimensions=resolved_settings.embedding_dimensions
                )
            rag_service = RagService(
                uow_factory=uow_factory,
                store=resource_store,
                storage=LocalDocumentStorage(
                    resolved_settings.document_storage_path,
                    max_bytes=resolved_settings.resource_max_bytes,
                ),
                embeddings=embedding_provider,
                web_fetcher=SafeWebPageFetcher(
                    max_bytes=resolved_settings.resource_max_bytes,
                    timeout_seconds=resolved_settings.web_fetch_timeout_seconds,
                ),
                clock=utc_now,
                user_id=DEFAULT_USER_ID,
                chunk_size=resolved_settings.resource_chunk_size,
                chunk_overlap=resolved_settings.resource_chunk_overlap,
            )
            lifespan_app.state.rag_service = rag_service
            lifespan_app.state.job_service = JobService(
                store=job_store,
                clock=utc_now,
                max_attempts=resolved_settings.job_max_attempts,
            )
            application_dependencies = ApplicationDependencies(
                uow_factory=uow_factory,
                review_scheduler=FsrsReviewScheduler(),
                curriculum_generator=curriculum_generator,
                resource_search=rag_service,
                review_exercise_generator=(
                    LlmReviewExerciseGenerator(structured_model)
                    if structured_model is not None
                    else None
                ),
            )
            lifespan_app.state.application_dependencies = application_dependencies
            async with open_agent_runtime(
                resolved_settings, application_dependencies, structured_model
            ) as agent_runtime:
                lifespan_app.state.agent_runtime = agent_runtime
                yield
        finally:
            if rag_service is not None:
                await rag_service.close()
            elif resource_store is not None:
                await resource_store.close()
            if job_store is not None:
                await job_store.close()
            if remote_embedding is not None:
                await remote_embedding.aclose()
            if model_provider is not None:
                await model_provider.aclose()
            await database.dispose()

    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        debug=resolved_settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings

    @app.middleware("http")
    async def observe_http(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "http_request_failed",
                extra={
                    "request_id": request_id,
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                },
            )
            raise
        duration_ms = round((perf_counter() - started) * 1000, 3)
        response.headers["X-Request-ID"] = request_id
        response.headers["Server-Timing"] = f"app;dur={duration_ms}"
        logger.info(
            "http_request_completed",
            extra={
                "request_id": request_id,
                "http_method": request.method,
                "http_path": request.url.path,
                "http_status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    app.include_router(api_router, prefix=resolved_settings.api_prefix)
    return app


app = create_app()
