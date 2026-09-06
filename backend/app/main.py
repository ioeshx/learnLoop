"""LearnLoop API application entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.execution import open_agent_runtime
from app.api.router import api_router
from app.application import ApplicationDependencies
from app.config import Settings, get_settings
from app.errors import register_error_handlers
from app.infrastructure.database import SqlAlchemyUnitOfWork, create_database
from app.infrastructure.llm import (
    DeepSeekModelProvider,
    LlmCurriculumGenerator,
    ModelProvider,
    StructuredModel,
)
from app.infrastructure.review import FsrsReviewScheduler
from app.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application with explicit, testable settings."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(lifespan_app: FastAPI) -> AsyncGenerator[None, None]:
        database = create_database(resolved_settings)
        model_provider: ModelProvider | None = None
        structured_model: StructuredModel | None = None
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
            application_dependencies = ApplicationDependencies(
                uow_factory=lambda: SqlAlchemyUnitOfWork(database.session_factory),
                review_scheduler=FsrsReviewScheduler(),
                curriculum_generator=curriculum_generator,
            )
            lifespan_app.state.application_dependencies = application_dependencies
            async with open_agent_runtime(
                resolved_settings, application_dependencies, structured_model
            ) as agent_runtime:
                lifespan_app.state.agent_runtime = agent_runtime
                yield
        finally:
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
