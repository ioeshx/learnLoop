"""LearnLoop API application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.application import ApplicationDependencies
from app.config import Settings, get_settings
from app.errors import register_error_handlers
from app.infrastructure.database import SqlAlchemyUnitOfWork, create_database
from app.infrastructure.review import FsrsReviewScheduler
from app.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application with explicit, testable settings."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(lifespan_app: FastAPI) -> AsyncIterator[None]:
        database = create_database(resolved_settings)
        lifespan_app.state.database = database
        lifespan_app.state.application_dependencies = ApplicationDependencies(
            uow_factory=lambda: SqlAlchemyUnitOfWork(database.session_factory),
            review_scheduler=FsrsReviewScheduler(),
        )
        try:
            yield
        finally:
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
