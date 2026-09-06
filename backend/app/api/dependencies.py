"""FastAPI dependencies for application services."""

from typing import Annotated, cast

from fastapi import Depends, Request

from app.application.services import ApplicationDependencies


async def get_application_dependencies(request: Request) -> ApplicationDependencies:
    return cast(ApplicationDependencies, request.app.state.application_dependencies)


ApplicationDependenciesDep = Annotated[
    ApplicationDependencies, Depends(get_application_dependencies)
]
